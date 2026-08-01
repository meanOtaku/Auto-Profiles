"""Guards the headless-first architecture against accidental GUI dependencies.

This project depends on ``opencv-python-headless`` (see ``pyproject.toml``),
not ``opencv-python``, specifically so it runs on displayless targets such
as a Jetson in a JetPack Ubuntu headless/server install. The headless wheel
has no GUI backend compiled in, so any accidental ``cv2.imshow``/
``cv2.waitKey``/``cv2.namedWindow`` call would raise at runtime on exactly
the platforms this project targets. This test statically guards against
that regression rather than requiring a display to catch it.
"""

from pathlib import Path

_SRC = Path(__file__).parents[2] / "src" / "face_profile"
_FORBIDDEN_SYMBOLS = ("cv2.imshow", "cv2.namedWindow", "cv2.waitKey", "cv2.startWindowThread")


def test_source_tree_never_calls_opencv_gui_functions() -> None:
    offenders = []
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for symbol in _FORBIDDEN_SYMBOLS:
            if symbol in text:
                offenders.append(f"{path.relative_to(_SRC)}: {symbol}")
    assert offenders == []


def test_opencv_headless_variant_is_the_pinned_dependency() -> None:
    pyproject = (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert "opencv-python-headless" in pyproject
    assert "opencv-python==" not in pyproject
