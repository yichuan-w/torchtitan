.PHONY: install lint build publish package-install force-install

install:
	poetry install

lint:
	poetry run flake8 brain_games

build:
	poetry build

publish:
	poetry publish --dry-run

package-install:
	python3 -m pip install --user dist/*.whl

force-install:
	python3 -m pip install --user --force-reinstall dist/*.whl
