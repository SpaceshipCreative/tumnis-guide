# Fixture README (P4-06)

Every token of the tagged block grammar (scripts/readme_test.py), used by
backend/tests/meta/test_readme_test.py. Line numbers matter: the tests name them.

```bash readme:env
TUMNIS_HOST=localhost
```

## Install

```bash readme:install:10
docker --version
```

```sh readme:install:20 timeout=900
echo installed
```

```bash readme:install:30 expect=^ready\b timeout=60
echo ready
```

```python
print("not a shell block, so untagged is fine")
```

```bash
echo "untagged inside Install: the coverage check names this line"
```

### Install on Coolify

```bash readme:manual
echo "a manual step: never run by CI"
```

## First run

```bash readme:first-run:10
echo first-run
```

```bash
echo "untagged in First run: the Install check passes it"
```

## Upgrade

```bash readme:upgrade:10 timeout=1200
echo upgrade
```
