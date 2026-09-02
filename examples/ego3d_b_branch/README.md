# Ego3D B branch acceptance example

This directory is a level-1 EgoAgentOS input: it contains a frozen baseline,
an explicit experiment hierarchy, a resource plan, the mathematical design,
tests, and integration-grade core code.

Build the deterministic tree and matrix:

```bash
ego-research-compile examples/ego3d_b_branch/input.yaml artifacts/ego3d-b-compiled
```

The compiler creates `experiment-tree.json`, `experiment-matrix.json`,
`resource-review.json`, and the combined `compile.json`. It does **not** launch
GPU work; a PASS resource review only allows the normal human approval gate to
be reached.

Truth boundary: the baseline metrics in `input.yaml` were supplied by the
operator. The B branch is a detailed design and core-code package; no B-branch
GPU result is claimed in this repository.
