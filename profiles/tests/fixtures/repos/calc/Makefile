# The skill harness copies this repository for each run and fills in the three values
# below (P2-12): the Python that runs the suite, the file each suite result is recorded
# in, and any extra test directory the case adds.
PYTHON := @HARNESS_PYTHON@
RECORD := @HARNESS_RECORD@
EXTRA_TESTS := @HARNESS_EXTRA_TESTS@

.PHONY: test
test:
	@PYTHONPATH=. $(PYTHON) -m pytest -q -p no:cacheprovider tests $(EXTRA_TESTS); \
	status=$$?; \
	if [ $$status -eq 0 ]; then result=green; else result=red; fi; \
	printf '{"server": "harness", "tool": "make_test", "arguments": {"result": "%s"}, "result": null}\n' "$$result" >> "$(RECORD)"; \
	exit $$status
