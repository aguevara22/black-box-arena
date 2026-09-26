import pytest

from arena import require_python


def test_old_interpreter_is_refused_with_an_actionable_message():
    with pytest.raises(SystemExit) as excinfo:
        require_python.check((3, 9, 6, "final", 0), "/usr/bin/python3")
    message = str(excinfo.value)
    assert "Python 3.11 or newer is required" in message
    assert "3.9.6" in message
    assert "/usr/bin/python3" in message
    assert "python3.11 -m venv .venv" in message


def test_floor_and_running_interpreter_pass():
    require_python.check((3, 11, 0, "final", 0), "x")
    require_python.check()
