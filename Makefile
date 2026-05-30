clear:
	rm -rf dist/
	rm -rf *.egg-info

format:
	black .

lint:
	pre-commit run -a -v

test:
	pip install .
	pytest

benchmark:
	python benchmarks/run_deadfixtures_benchmark.py --case medium --rounds 5 --output .benchmarks/results/current-medium.json

benchmark-algorithm:
	python benchmarks/run_deadfixtures_algorithm_benchmark.py --case medium --rounds 50 --output .benchmarks/results/current-medium-algorithm.json

benchmark-matrix:
	python benchmarks/run_pytest_matrix.py --case medium --rounds 5

test-release: clear
	python setup.py sdist bdist_wheel
	twine upload dist/* -r testpypi

release: clear
	git tag `python setup.py -q version`
	git push origin `python setup.py -q version`
	python setup.py sdist bdist_wheel
	twine upload dist/* -r pypi

setup:
	pip install -U -r requirements-dev.txt
