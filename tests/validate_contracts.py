"""
Basic contract validation scaffold for Hermes OS.

Run:
    python tests/validate_contracts.py

Requires:
    pip install jsonschema
"""

from pathlib import Path
import json

try:
    from jsonschema import Draft202012Validator
except ImportError:
    raise SystemExit("Please install jsonschema: pip install jsonschema")

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = ROOT / "examples"

pairs = [
    ("business.schema.json", "business.example.json"),
]

errors = 0

for schema_name, example_name in pairs:
    schema = json.loads((CONTRACTS / schema_name).read_text())
    example = json.loads((EXAMPLES / example_name).read_text())

    validator = Draft202012Validator(schema)
    validation_errors = sorted(validator.iter_errors(example), key=lambda e: e.path)

    if validation_errors:
        print(f"✗ {example_name}")
        for err in validation_errors:
            print(f"  - {err.message}")
        errors += len(validation_errors)
    else:
        print(f"✓ {example_name}")

if errors:
    raise SystemExit(f"Validation failed with {errors} error(s).")

print("\nAll example payloads are valid.")
