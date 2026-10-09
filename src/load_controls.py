"""
Load and validate the ITGC control library (controls/controls.yaml).
"""

from pathlib import Path
import yaml

CONTROLS_FILE = Path(__file__).resolve().parent.parent / "controls" / "controls.yaml"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

REQUIRED_FIELDS = [
    "id", "name", "domain", "objective", "control_description",
    "test_procedure", "evidence", "frameworks", "risk_rating",
    "escalation_rule", "recommendation_guidance",
]
REQUIRED_FRAMEWORKS = ["sox_itgc", "cobit_2019", "nist_csf_2"]
VALID_RATINGS = ["Critical", "High", "Medium", "Low"]


def load_controls():
    """Return the full control library as a dictionary."""
    with open(CONTROLS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_control(control_id):
    """Return a single control definition by its ID, e.g. 'AC-01'."""
    for control in load_controls()["controls"]:
        if control["id"] == control_id:
            return control
    raise KeyError(f"Control {control_id} not found in {CONTROLS_FILE.name}")


def validate():
    """Check every control is complete. Returns a list of problems found."""
    library = load_controls()
    problems = []
    seen_ids = set()

    for c in library["controls"]:
        cid = c.get("id", "<missing id>")

        if cid in seen_ids:
            problems.append(f"{cid}: duplicate control ID")
        seen_ids.add(cid)

        for field in REQUIRED_FIELDS:
            if not c.get(field):
                problems.append(f"{cid}: missing '{field}'")

        for fw in REQUIRED_FRAMEWORKS:
            if not c.get("frameworks", {}).get(fw):
                problems.append(f"{cid}: missing framework mapping '{fw}'")

        if c.get("risk_rating") not in VALID_RATINGS:
            problems.append(f"{cid}: invalid risk rating '{c.get('risk_rating')}'")

        for evidence_file in c.get("evidence", []):
            if not (DATA_DIR / evidence_file).exists():
                problems.append(f"{cid}: evidence file '{evidence_file}' not found in data/")

    return problems


if __name__ == "__main__":
    library = load_controls()
    print(f"Audit: {library['audit']['name']}")
    print(f"Period: {library['audit']['period_start']} to {library['audit']['period_end']}\n")

    for c in library["controls"]:
        print(f"  {c['id']}  {c['name']:<45} {c['risk_rating']:<8} {c['frameworks']['cobit_2019'].split()[0]}")

    problems = validate()
    if problems:
        print(f"\nValidation FAILED ({len(problems)} problems):")
        for p in problems:
            print(f"  - {p}")
    else:
        print(f"\nValidation passed: {len(library['controls'])} controls complete.")