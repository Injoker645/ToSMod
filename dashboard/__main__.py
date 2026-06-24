"""Run with: python -m dashboard (from the ToSMod repo root)."""

from dashboard.app import app

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
