"""Explicit non-production credentials for authenticated API tests."""

TEST_OPERATOR_KEY = "test-only-operator-key-0123456789abcdef"
TEST_OPERATOR_ID = "test.operator"
TEST_AUTHORIZATION_HEADERS = {
    "Authorization": "Bearer %s" % TEST_OPERATOR_KEY,
}
