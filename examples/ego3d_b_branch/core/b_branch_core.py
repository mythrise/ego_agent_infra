"""Reference core for the Ego3D B branch.

Integration target: ``src/ego3d_wm/globalroot_se3/`` in ego3d_wm_global.
The caller must use the repository's audited T_A_B convention and released
prefix-8 features. This file contains no dataset loader and cannot silently
read validation GT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import torch
from torch import Tensor, nn

from ego3d_wm.globalroot_se3.se3 import invert_se3, se3_exp, so3_geodesic


def _check_transform(value: Tensor, name: str) -> None:
    if value.shape[-2:] != (4, 4) or not value.is_floating_point():
        raise ValueError(f"{name} must end in a floating [4,4] transform")
    if not bool(torch.isfinite(value).all().item()):
        raise ValueError(f"{name} must be finite")
    expected = torch.tensor((0.0, 0.0, 0.0, 1.0), device=value.device, dtype=value.dtype)
    if not torch.allclose(value[..., 3, :], expected.expand_as(value[..., 3, :]), atol=1e-6, rtol=0):
        raise ValueError(f"{name} must be homogeneous")


def head_camera_from_training_target(T_camera_root_gt: Tensor, T_root_head: Tensor) -> Tensor:
    """Return per-frame T_head_camera using *training-only* root targets.

    T_A_B maps B coordinates into A. Therefore:
      T_head_camera = inv(T_root_head) @ inv(T_camera_root_gt).
    """

    _check_transform(T_camera_root_gt, "T_camera_root_gt")
    _check_transform(T_root_head, "T_root_head")
    if T_camera_root_gt.shape != T_root_head.shape:
        raise ValueError("root and head transforms must have the same shape")
    return invert_se3(T_root_head) @ invert_se3(T_camera_root_gt)


def _rotation_medoid(rotations: Tensor, max_candidates: int = 256) -> Tensor:
    """Robust, deterministic SO(3) medoid; avoids averaging quaternions across antipodes."""

    flat = rotations.reshape(-1, 3, 3)
    if flat.shape[0] == 0:
        raise ValueError("at least one rotation is required")
    step = max(1, flat.shape[0] // max_candidates)
    candidates = flat[::step][:max_candidates]
    pairwise = so3_geodesic(candidates[:, None], flat[None, :])
    return candidates[pairwise.median(dim=1).values.argmin()]


@dataclass(frozen=True)
class HeadRigFit:
    T_head_camera: Tensor
    sample_count: int
    translation_mad_m: Tensor
    calibration_fold: int


class RobustHeadRigCalibrator:
    """Fit one head-camera transform using training takes only.

    A production loader must instantiate one fit per OOF target fold and reject
    any sample whose take UID belongs to that target fold.
    """

    @staticmethod
    def fit(
        T_camera_root_gt: Tensor,
        T_root_head: Tensor,
        valid: Tensor,
        *,
        calibration_fold: int,
    ) -> HeadRigFit:
        if valid.shape != T_camera_root_gt.shape[:-2] or valid.dtype is not torch.bool:
            raise ValueError("valid must be boolean and match transform rows")
        samples = head_camera_from_training_target(T_camera_root_gt[valid], T_root_head[valid])
        if samples.shape[0] < 32:
            raise ValueError("head-rig fit requires at least 32 valid training frames")
        translation = samples[..., :3, 3]
        center = translation.median(dim=0).values
        mad = (translation - center).abs().median(dim=0).values
        rotation = _rotation_medoid(samples[..., :3, :3])
        fit = torch.eye(4, dtype=samples.dtype, device=samples.device)
        fit[:3, :3] = rotation
        fit[:3, 3] = center
        return HeadRigFit(fit, int(samples.shape[0]), mad, calibration_fold)


def camera_root_from_head(T_head_camera: Tensor, T_root_head_pred: Tensor) -> Tensor:
    """B1 initialization: T_camera_root = inv(T_head_camera) @ inv(T_root_head)."""

    _check_transform(T_head_camera, "T_head_camera")
    _check_transform(T_root_head_pred, "T_root_head_pred")
    return invert_se3(T_head_camera) @ invert_se3(T_root_head_pred)


class BoundedRootResidual(nn.Module):
    """B2/B3/B4 residual with explicit translation and rotation bounds.

    Translation is expressed in an orthonormal gravity basis. Rotation is a
    left-composed SO(3) update in the same basis. Zero initialization makes the
    first forward pass exactly the analytic B1 transform.
    """

    def __init__(self, feature_dim: int, hidden_dim: int = 192) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(feature_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.GRU(hidden_dim, hidden_dim, num_layers=2, batch_first=True),
        )
        self.twist = nn.Linear(hidden_dim, 6)
        self.logvar = nn.Linear(hidden_dim, 6)
        nn.init.zeros_(self.twist.weight)
        nn.init.zeros_(self.twist.bias)
        nn.init.zeros_(self.logvar.weight)
        nn.init.zeros_(self.logvar.bias)
        self.register_buffer("translation_limit_m", torch.tensor((0.15, 0.15, 0.30)))
        self.register_buffer(
            "rotation_limit_rad", torch.deg2rad(torch.tensor((8.0, 8.0, 15.0)))
        )

    def forward(
        self,
        features: Tensor,
        T_camera_root_init: Tensor,
        gravity_basis_camera: Tensor,
        *,
        enable_translation: bool,
        enable_rotation: bool,
    ) -> Dict[str, Tensor]:
        if features.ndim != 3:
            raise ValueError("features must be [B,T,D]")
        _check_transform(T_camera_root_init, "T_camera_root_init")
        if gravity_basis_camera.shape != (*features.shape[:2], 3, 3):
            raise ValueError("gravity_basis_camera must be [B,T,3,3]")
        encoded = self.encoder[0](features)
        encoded = self.encoder[1](encoded)
        encoded = self.encoder[2](encoded)
        hidden, _ = self.encoder[3](encoded)
        raw = self.twist(hidden)
        delta_t_g = torch.tanh(raw[..., :3]) * self.translation_limit_m
        delta_w_g = torch.tanh(raw[..., 3:]) * self.rotation_limit_rad
        if not enable_translation:
            delta_t_g = torch.zeros_like(delta_t_g)
        if not enable_rotation:
            delta_w_g = torch.zeros_like(delta_w_g)

        delta_t_c = torch.einsum("...ij,...j->...i", gravity_basis_camera, delta_t_g)
        delta_w_c = torch.einsum("...ij,...j->...i", gravity_basis_camera, delta_w_g)
        rotation_delta = se3_exp(
            torch.cat((torch.zeros_like(delta_w_c), delta_w_c), dim=-1)
        )[..., :3, :3]
        result = T_camera_root_init.clone()
        result[..., :3, :3] = rotation_delta @ T_camera_root_init[..., :3, :3]
        result[..., :3, 3] = T_camera_root_init[..., :3, 3] + delta_t_c
        return {
            "T_camera_root": result,
            "delta_translation_gravity_m": delta_t_g,
            "delta_rotation_gravity_rad": delta_w_g,
            "logvar": self.logvar(hidden).clamp(-8.0, 6.0),
        }


def heteroscedastic_huber(error: Tensor, logvar: Tensor, delta: float) -> Tensor:
    absolute = error.abs()
    huber = torch.where(absolute <= delta, 0.5 * error.square(), delta * (absolute - 0.5 * delta))
    return (torch.exp(-logvar) * huber + 0.5 * logvar).mean()


class ConfidenceGatedWristSeam(nn.Module):
    """B6 moves body wrist and its full hand tree by one shared residual."""

    BODY_WRISTS = (9, 10)
    HAND_WRISTS = (17, 38)
    HAND_SLICES = (slice(17, 38), slice(38, 59))

    def __init__(self, feature_dim: int, hidden_dim: int = 128, limit_m: float = 0.12) -> None:
        super().__init__()
        self.net = nn.Sequential(nn.Linear(feature_dim, hidden_dim), nn.GELU(), nn.LayerNorm(hidden_dim))
        self.head = nn.Linear(hidden_dim, 3)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)
        self.limit_m = float(limit_m)

    def forward(
        self,
        joints_root: Tensor,
        features: Tensor,
        detector_confidence: Tensor,
        visible: Tensor,
    ) -> Dict[str, Tensor]:
        if joints_root.shape[-2:] != (59, 3) or features.shape[-2] != 2:
            raise ValueError("expected Full-59 joints and two hand feature rows")
        if detector_confidence.shape != features.shape[:-1] or visible.shape != features.shape[:-1]:
            raise ValueError("confidence and visibility must match [B,T,2]")
        gate = detector_confidence.clamp(0, 1).square() * visible.to(features.dtype)
        residual = torch.tanh(self.head(self.net(features))) * self.limit_m * gate[..., None]
        corrected = joints_root.clone()
        for side, (body_wrist, hand_slice) in enumerate(zip(self.BODY_WRISTS, self.HAND_SLICES)):
            delta = residual[..., side, :]
            corrected[..., body_wrist, :] += delta
            corrected[..., hand_slice, :] += delta[..., None, :]
        seam = torch.stack(
            (
                corrected[..., self.BODY_WRISTS[0], :] - corrected[..., self.HAND_WRISTS[0], :],
                corrected[..., self.BODY_WRISTS[1], :] - corrected[..., self.HAND_WRISTS[1], :],
            ),
            dim=-2,
        )
        return {"joints_root": corrected, "wrist_residual_m": residual, "seam_vector_m": seam}


def wrist_objective(
    prediction: Dict[str, Tensor],
    target_joints_root: Tensor,
    joint_valid: Tensor,
    *,
    seam_weight: float = 2.0,
    temporal_weight: float = 0.2,
) -> Tensor:
    wrists = torch.tensor((9, 10, 17, 38), device=target_joints_root.device)
    error = prediction["joints_root"].index_select(-2, wrists) - target_joints_root.index_select(-2, wrists)
    mask = joint_valid.index_select(-1, wrists)[..., None]
    data = error.masked_select(mask).abs().mean()
    seam = prediction["seam_vector_m"].norm(dim=-1).mean()
    residual = prediction["wrist_residual_m"]
    temporal = (residual[:, 1:] - residual[:, :-1]).abs().mean() if residual.shape[1] > 1 else residual.new_zeros(())
    return data + seam_weight * seam + temporal_weight * temporal
