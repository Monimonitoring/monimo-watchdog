import json

from state import FileStateStore, from_env


def test_파일_저장소는_없으면_None_쓰면_그대로_읽힌다(tmp_path):
    store = FileStateStore(str(tmp_path / "state.json"))

    assert store.load() is None
    store.save({"active": {"canary": {"since": "2026-10-29T12:33:00Z", "detail": "x"}}, "updated_at": "2026-10-29T12:33:00Z"})

    assert store.load()["active"]["canary"]["since"] == "2026-10-29T12:33:00Z"
    assert "tmp" not in "".join(p.name for p in tmp_path.iterdir())  # 바꿔 끼운 뒤 임시 파일은 남지 않는다


def test_환경변수로_저장소를_고른다(tmp_path):
    assert from_env({}) is None
    assert isinstance(from_env({"STATE_FILE": str(tmp_path / "s.json")}), FileStateStore)


def test_파일이_깨져_있으면_읽기가_던지고_handler_가_기억_없음으로_다룬다(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")

    try:
        FileStateStore(str(path)).load()
    except json.JSONDecodeError:
        return
    raise AssertionError("깨진 파일은 예외여야 한다")
