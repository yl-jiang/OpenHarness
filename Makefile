VENV_BIN ?= .venv/bin

.PHONY: all install install-dev test test-cov cov-html lint format typecheck check build clean

# 默认执行测试
all: test

# -----------------
# 依赖安装
# -----------------

# 安装开发依赖
install:
	pip install -e ".[dev]"

# 执行开发环境安装脚本
install-dev:
	bash scripts/install_dev.sh

# -----------------
# 测试与覆盖率
# -----------------

# 执行所有测试
test:
	$(VENV_BIN)/pytest

# 执行测试并在终端输出覆盖率报告
test-cov:
	$(VENV_BIN)/pytest --cov=src/openharness --cov=ohmo

# 执行测试并生成 HTML 覆盖率报告
cov-html:
	$(VENV_BIN)/pytest --cov=src/openharness --cov=ohmo --cov-report=html

# -----------------
# 代码质量检查
# -----------------

# 检查代码规范 (使用 ruff)
lint:
	$(VENV_BIN)/ruff check src ohmo tests

# 自动格式化代码 (使用 ruff)
format:
	$(VENV_BIN)/ruff check --fix src ohmo tests
	$(VENV_BIN)/ruff format src ohmo tests

# 类型检查 (使用 mypy)
typecheck:
	$(VENV_BIN)/mypy src ohmo tests

# 运行所有代码质量检查 (格式化、lint、类型检查)
check: format lint typecheck

# -----------------
# 构建与清理
# -----------------

# 构建 Python 安装包 (wheel/sdist)
build:
	python -m build

# 清理缓存和构建产物
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build *.egg-info
