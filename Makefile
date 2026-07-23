PY ?= python3
PREFIX ?= $(HOME)/.local

.PHONY: help test check install install-tb uninstall clean version

help:
	@echo "make test        run unit tests"
	@echo "make check       run tests, compile check, and version check"
	@echo "make install     link tmux-tui into PREFIX/bin"
	@echo "make install-tb  link tmux-tui and tb into PREFIX/bin"
	@echo "make uninstall   remove managed command links"

test:
	$(PY) -m unittest discover -s tests

check: test
	$(PY) -m py_compile tmux_tui.py
	@test "$$($(PY) tmux_tui.py --version)" = "tmux-tui $$(cat VERSION)"

install:
	@set -eu; \
	bin="$(PREFIX)/bin"; \
	target="$(CURDIR)/tmux_tui.py"; \
	link="$$bin/tmux-tui"; \
	[ ! -L "$$bin" ] || { echo "refusing symlinked install directory: $$bin" >&2; exit 1; }; \
	mkdir -p "$$bin"; \
	[ -d "$$bin" ] || { echo "install directory is not a directory: $$bin" >&2; exit 1; }; \
	if [ -e "$$link" ] || [ -L "$$link" ]; then \
		[ -L "$$link" ] || { echo "refusing to replace non-symlink: $$link" >&2; exit 1; }; \
		[ "$$(readlink -- "$$link")" = "$$target" ] \
			|| { echo "refusing to replace unmanaged symlink: $$link" >&2; exit 1; }; \
	fi; \
	temporary="$$bin/.tmux-tui.$$$$.tmp"; \
	[ ! -e "$$temporary" ] && [ ! -L "$$temporary" ] \
		|| { echo "temporary link already exists: $$temporary" >&2; exit 1; }; \
	trap 'rm -f -- "$$temporary"' EXIT; \
	ln -s -- "$$target" "$$temporary"; \
	mv -Tf -- "$$temporary" "$$link"; \
	trap - EXIT
	@echo "linked $(PREFIX)/bin/tmux-tui"

install-tb:
	@test -f "$(CURDIR)/tmux-cli/tb.py" || { echo "tmux-cli submodule is missing" >&2; exit 1; }
	@set -eu; \
	target="$(CURDIR)/tmux-cli/tb.py"; \
	link="$(PREFIX)/bin/tb"; \
	if [ -e "$$link" ] || [ -L "$$link" ]; then \
		[ -L "$$link" ] || { echo "refusing to replace non-symlink: $$link" >&2; exit 1; }; \
		[ "$$(readlink -- "$$link")" = "$$target" ] \
			|| { echo "refusing to replace unmanaged symlink: $$link" >&2; exit 1; }; \
	fi
	@$(MAKE) --no-print-directory install PREFIX="$(PREFIX)"
	@set -eu; \
	bin="$(PREFIX)/bin"; \
	target="$(CURDIR)/tmux-cli/tb.py"; \
	link="$$bin/tb"; \
	if [ -e "$$link" ] || [ -L "$$link" ]; then \
		[ -L "$$link" ] || { echo "refusing to replace non-symlink: $$link" >&2; exit 1; }; \
		[ "$$(readlink -- "$$link")" = "$$target" ] \
			|| { echo "refusing to replace unmanaged symlink: $$link" >&2; exit 1; }; \
	fi; \
	temporary="$$bin/.tb.$$$$.tmp"; \
	[ ! -e "$$temporary" ] && [ ! -L "$$temporary" ] \
		|| { echo "temporary link already exists: $$temporary" >&2; exit 1; }; \
	trap 'rm -f -- "$$temporary"' EXIT; \
	ln -s -- "$$target" "$$temporary"; \
	mv -Tf -- "$$temporary" "$$link"; \
	trap - EXIT
	@echo "linked $(PREFIX)/bin/tb"

uninstall:
	@set -eu; \
	remove_managed() { \
		target="$$1"; link="$$2"; \
		[ -e "$$link" ] || [ -L "$$link" ] || return 0; \
		if [ -L "$$link" ] && [ "$$(readlink -- "$$link")" = "$$target" ]; then \
			rm -f -- "$$link"; \
		else \
			echo "leaving unmanaged path untouched: $$link" >&2; \
		fi; \
	}; \
	remove_managed "$(CURDIR)/tmux_tui.py" "$(PREFIX)/bin/tmux-tui"; \
	remove_managed "$(CURDIR)/tmux-cli/tb.py" "$(PREFIX)/bin/tb"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

version:
	@$(PY) tmux_tui.py --version
