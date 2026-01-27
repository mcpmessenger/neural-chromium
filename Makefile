# Neural-Chromium Makefile
# Reproducible benchmarks and common tasks

.PHONY: benchmark benchmark-quick install-deps help

help:
	@echo "Neural-Chromium Development Commands"
	@echo "===================================="
	@echo ""
	@echo "  make benchmark       - Run full production benchmark (10 runs per task)"
	@echo "  make benchmark-quick - Run quick benchmark (3 runs per task)"
	@echo "  make install-deps    - Install Python dependencies"
	@echo "  make help            - Show this help message"

install-deps:
	pip install playwright grpcio grpcio-tools protobuf pillow requests
	playwright install chromium

benchmark:
	@echo "Running production benchmark (10 runs per task)..."
	@echo "This will take approximately 10-15 minutes."
	python src/benchmark_production.py

benchmark-quick:
	@echo "Running quick benchmark (3 runs per task)..."
	@RUNS_PER_TASK=3 python src/benchmark_production.py
