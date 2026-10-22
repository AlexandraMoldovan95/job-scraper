from dotenv import dotenv_values

from jobscraper.keys import SEARCHAPI_API_KEY, KeyStore, mask


def make_store(tmp_path, environ=None):
    return KeyStore({}, env_path=tmp_path / ".env", environ={} if environ is None else environ)


def test_missing_key(tmp_path):
    store = make_store(tmp_path)
    assert store.get(SEARCHAPI_API_KEY) is None
    assert store.lookup(SEARCHAPI_API_KEY) is None


def test_session_key_wins_over_env(tmp_path):
    store = make_store(tmp_path, environ={SEARCHAPI_API_KEY: "from-env"})
    assert store.lookup(SEARCHAPI_API_KEY) == ("from-env", "env")

    store.set_for_session(SEARCHAPI_API_KEY, "  from-ui  ")
    assert store.lookup(SEARCHAPI_API_KEY) == ("from-ui", "session")

    store.forget_session(SEARCHAPI_API_KEY)
    assert store.lookup(SEARCHAPI_API_KEY) == ("from-env", "env")


def test_blank_session_value_clears_it(tmp_path):
    store = make_store(tmp_path)
    store.set_for_session(SEARCHAPI_API_KEY, "abc")
    store.set_for_session(SEARCHAPI_API_KEY, "   ")
    assert store.get(SEARCHAPI_API_KEY) is None


def test_save_to_env_writes_file_and_keeps_other_lines(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("ANTHROPIC_API_KEY=sk-ant-existing\n", encoding="utf-8")
    environ: dict[str, str] = {}
    store = KeyStore({}, env_path=env_path, environ=environ)
    store.set_for_session(SEARCHAPI_API_KEY, "typed-in-ui")

    store.save_to_env(SEARCHAPI_API_KEY, "secret-123")

    assert dotenv_values(env_path) == {"ANTHROPIC_API_KEY": "sk-ant-existing", SEARCHAPI_API_KEY: "secret-123"}
    assert store.lookup(SEARCHAPI_API_KEY) == ("secret-123", "env")


def test_save_to_env_creates_missing_file(tmp_path):
    store = make_store(tmp_path)
    store.save_to_env(SEARCHAPI_API_KEY, "secret-123")
    assert dotenv_values(tmp_path / ".env") == {SEARCHAPI_API_KEY: "secret-123"}


def test_remove_from_env(tmp_path):
    store = make_store(tmp_path)
    store.save_to_env(SEARCHAPI_API_KEY, "secret-123")
    store.remove_from_env(SEARCHAPI_API_KEY)
    assert store.get(SEARCHAPI_API_KEY) is None
    assert SEARCHAPI_API_KEY not in dotenv_values(tmp_path / ".env")


def test_mask_hides_the_middle():
    assert mask("sk-ant-api03-abcdefgh1234") == "sk-a…1234"
    assert mask("short") == "•••••"
