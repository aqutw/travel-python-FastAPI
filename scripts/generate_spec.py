#!/usr/bin/env python3
"""Refresh request schemas from the Pydantic models used by FastAPI."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models import WishlistInput, TripInput, ReviewInput, QuoteInput


def main():
    path = ROOT / 'app/openapi.json'
    spec = json.loads(path.read_text())
    schemas = spec['components']['schemas']
    for name, model in [('WishlistRequest', WishlistInput), ('TripRequest', TripInput),
                        ('ReviewRequest', ReviewInput), ('QuoteRequest', QuoteInput)]:
        shape = model.model_json_schema(ref_template='#/components/schemas/Input{model}')
        for nested, nested_schema in shape.pop('$defs', {}).items():
            schemas['Input' + nested] = nested_schema
        schemas[name] = shape
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    print('Generated OpenAPI request schemas from Pydantic models.')


if __name__ == '__main__':
    main()
