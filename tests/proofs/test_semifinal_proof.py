import json

from scripts.build_semifinal_proof import checksum_bytes, proof_bytes, sha256_bytes


def test_semifinal_proof_is_deterministic_and_truth_labelled() -> None:
    first = proof_bytes()
    second = proof_bytes()
    assert first == second
    proof = json.loads(first)
    assert proof["local_executable_proofs"]["rxp"]["status"] == "PASS"
    skill = proof["local_executable_proofs"]["skill_runtime"]
    assert skill["status"] == "PASS"
    assert skill["research_plan_invocation"]["trace"]["status"] == "PASS"
    assert skill["repeat_invocation_equal"] is True
    boundaries = proof["external_runtime_boundaries"]
    assert boundaries["live_agentteams"] == {
        "status": "SKIP",
        "verification": "UNVERIFIED",
        "reason": (
            "No live AgentTeams Controller/team/workers and bound scenario credentials "
            "were used by this proof build."
        ),
    }
    for name in ("polardb_deployment", "pitr_restore", "application_docker_image"):
        assert boundaries[name]["status"] == "NOT_RUN"
        assert boundaries[name]["verification"] == "UNVERIFIED"
    assert checksum_bytes(first).decode("ascii") == (
        "%s  semifinal-local-proof.json\n" % sha256_bytes(first)
    )
