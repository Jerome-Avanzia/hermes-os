# Hermes OS


Hermes OS is an AI-first operating system for building, operating, and scaling digital businesses.


---


## Repository Structure


```text
docs/         Architecture and design documentation
specs/        Business object specifications
contracts/    JSON Schema contracts
api/          OpenAPI definitions
examples/     Canonical example payloads
tests/        Contract validation
.github/      GitHub Actions workflows
```


---


## Quick Start


### Install dependencies


```bash
pip install jsonschema
```


### Validate contracts


```bash
make validate
```


### Run tests


```bash
make test
```


---


## Core Principles


- Contracts before code
- Documentation as architecture
- Automation by default
- Version everything
- Validate continuously


---


## Development Workflow


1. Update specifications in `specs/`
2. Update JSON Schemas in `contracts/`
3. Update API definitions in `api/`
4. Add or update examples in `examples/`
5. Run validation:


```bash
make validate
```


6. Commit changes
7. Push to GitHub


---


## Continuous Integration


Every push and pull request automatically validates example payloads against the JSON Schemas using GitHub Actions.


---


## License


Private repository.