# Hermes OS Makefile

.PHONY: help validate test lint clean

help:
	@echo "Hermes OS Developer Commands"
	@echo ""
	@echo "  make validate   Validate JSON examples against contracts"
	@echo "  make test       Run validation suite"
	@echo "  make lint       Placeholder for future linting"
	@echo "  make clean      Remove Python cache files"

validate:
	python tests/validate_contracts.py

test: validate

lint:
	@echo "No lint rules configured yet."

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
