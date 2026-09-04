import os

from chronos_repro.envfile import load_env_file


def test_load_env_file_without_returning_values(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("# secret\nDEEPSEEK_API_KEY=test-only\n", encoding="utf-8")
    assert load_env_file(path) == ["DEEPSEEK_API_KEY"]
    assert os.environ["DEEPSEEK_API_KEY"] == "test-only"
