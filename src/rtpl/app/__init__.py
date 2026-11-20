"""Qt desktop application."""

# Tooltips are plain text and Qt does not wrap them, so break each line by
# hand ("\n") at this many characters or fewer: about half the width of the
# smallest supported screen (1024 pixels). tests/test_app_smoke.py checks it.
TIP_WIDTH = 70
