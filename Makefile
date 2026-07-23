PY ?= python3
PREFIX ?= $(HOME)/.local

.PHONY: help test install install-tb uninstall clean version

help:
	@echo "make test        run unit tests"
	@echo "make install     link tmux-tui into PREFIX/bin"
	@echo "make install-tb  link tmux-tui and tb into PREFIX/bin"
	@echo "make uninstall   remove managed command links"

test:
	$(PY) -m unittest discover -s tests

install:
	@mkdir -p "$(PREFIX)/bin"
	@ln -sfn "$(CURDIR)/tmux_tui.py" "$(PREFIX)/bin/tmux-tui"
	@echo "linked $(PREFIX)/bin/tmux-tui"

install-tb: install
	@test -f "$(CURDIR)/tmux-cli/tb.py" || { echo "tmux-cli submodule is missing" >&2; exit 1; }
	@ln -sfn "$(CURDIR)/tmux-cli/tb.py" "$(PREFIX)/bin/tb"
	@echo "linked $(PREFIX)/bin/tb"

uninstall:
	@for link in "$(PREFIX)/bin/tmux-tui" "$(PREFIX)/bin/tb"; do \
		if [ -L "$$link" ]; then rm -f "$$link"; fi; \
	done

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

version:
	@$(PY) tmux_tui.py --version
