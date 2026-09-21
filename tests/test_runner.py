import threading
from pathlib import Path

from mt.runner import JobOptions, collect_files, run_job


def make_tree(root: Path) -> None:
    (root / "a.txt").write_text("Hello", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("World", encoding="utf-8")
    (root / "sub" / "skip.exe").write_bytes(b"\x00")
    (root / "~$lock.xlsx").write_bytes(b"")


def test_collect_files_recursive_and_filtered(tmp_path):
    make_tree(tmp_path)
    assert [p.name for p in collect_files(tmp_path)] == ["a.txt", "b.txt"]
    assert collect_files(tmp_path / "a.txt") == [tmp_path / "a.txt"]
    assert collect_files(tmp_path / "sub" / "skip.exe") == []


def test_folder_separate_layout(tmp_path, cache_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir(); make_tree(src)
    st = run_job(JobOptions(input=src, output=out, targets=["hi", "pa"], cache_path=cache_path))
    assert st.state == "done" and st.files_done == 4 and not st.errors
    assert (out / "hi" / "a_hi.txt").read_text(encoding="utf-8") == "[hi] Hello"
    assert (out / "pa" / "sub" / "b_pa.txt").read_text(encoding="utf-8") == "[pa] World"


def test_combined_layout_and_resume(tmp_path, cache_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir(); make_tree(src)
    opts = JobOptions(input=src, output=out, targets=["hi"], mode="combined", cache_path=cache_path)
    st = run_job(opts)
    assert st.files_done == 2 and (out / "sub" / "b_multi.txt").exists()
    st2 = run_job(opts)                                       # resume: nothing re-done
    assert st2.state == "done" and st2.requests_made == 0 and any("skip" in l for l in st2.log)
    st3 = run_job(JobOptions(**{**opts.__dict__, "force": True}))
    assert st3.files_done == 2 and not any("skip" in l for l in st3.log)


def test_bad_file_does_not_stop_batch(tmp_path, cache_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    (src / "ok.txt").write_text("fine", encoding="utf-8")
    (src / "broken.xlsx").write_bytes(b"not a workbook")
    st = run_job(JobOptions(input=src, output=out, targets=["hi"], cache_path=cache_path))
    assert st.state == "done" and len(st.errors) == 1 and "broken.xlsx" in st.errors[0]
    assert (out / "hi" / "ok_hi.txt").exists()


def test_stop_event_cancels(tmp_path, cache_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    for i in range(3):
        (src / f"{i}.txt").write_text("x", encoding="utf-8")
    stop = threading.Event(); stop.set()
    st = run_job(JobOptions(input=src, output=out, targets=["hi"], cache_path=cache_path), stop_event=stop)
    assert st.state == "stopped" and st.files_done == 0


def test_empty_input_is_an_error(tmp_path, cache_path):
    (tmp_path / "empty").mkdir()
    st = run_job(JobOptions(input=tmp_path / "empty", output=tmp_path / "o", targets=["hi"], cache_path=cache_path))
    assert st.state == "error" and "No supported files" in st.errors[0]


def test_glossary_applied(tmp_path, cache_path):
    g = tmp_path / "g.csv"; g.write_text("term,hi\nVeda,वेदा\n", encoding="utf-8")
    src = tmp_path / "t.txt"; src.write_text("Veda soap", encoding="utf-8")
    st = run_job(JobOptions(input=src, output=tmp_path / "o", targets=["hi"], cache_path=cache_path, glossary=g))
    assert st.state == "done"
    assert (tmp_path / "o" / "hi" / "t_hi.txt").read_text(encoding="utf-8") == "[hi] वेदा soap"
