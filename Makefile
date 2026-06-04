VENV_BIN ?= .venv/bin
ONBOARD_ARGS ?=
RESTART_CLEAN ?= scripts/restart_clean.sh

.PHONY: all install install-dev test test-cov cov-html lint format typecheck check build clean \
        onboard solo-gw wolo-gw stop restart clean-runtime

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

# -----------------
# 运行时生命周期
# -----------------
#
# 启动类 target 都会先调用 `$(RESTART_CLEAN)` 把旧的 gateway / onboard /
# cron 调度器守护进程杀掉、把 __pycache__ 清掉，再启动新进程。这样就不会
# 出现"前台重起了，后台常驻进程还在跑老代码"的情况。

# 一键构建前端并前台启动 onboard（自动先停掉旧的常驻进程并清缓存）
onboard:
	@$(RESTART_CLEAN) --quiet
	npm --prefix onboard/frontend run build
	uv run onboard run $(ONBOARD_ARGS)

# 启动 solo gateway（自动先停掉旧的常驻进程并清缓存）
solo-gw:
	@$(RESTART_CLEAN) --quiet
	uv run solo gateway run

# 启动 wolo gateway（自动先停掉旧的常驻进程并清缓存）
wolo-gw:
	@$(RESTART_CLEAN) --quiet
	uv run wolo gateway run

# 仅停止所有常驻进程（wolo gateway + solo gateway + onboard + cron 调度器守护进程），不清缓存
stop:
	@$(RESTART_CLEAN) --no-pyc

# 停止所有常驻进程 + 清缓存 + 重启 onboard / solo-gw / wolo-gw（三件套）
# 注意：onboard 不会重建前端；如需重 build 前端请单独跑 `make onboard`
restart:
	@$(RESTART_CLEAN)
	@echo "[restart] starting onboard + solo-gw + wolo-gw as daemons..."
	@mkdir -p /tmp/openharness
	@nohup uv run onboard run $(ONBOARD_ARGS) >/tmp/openharness/onboard.log 2>&1 & echo "onboard     pid=$$!  log=/tmp/openharness/onboard.log"
	@nohup uv run solo gateway run      >/tmp/openharness/solo-gw.log  2>&1 & echo "solo-gw     pid=$$!  log=/tmp/openharness/solo-gw.log"
	@nohup uv run wolo gateway run      >/tmp/openharness/wolo-gw.log  2>&1 & echo "wolo-gw     pid=$$!  log=/tmp/openharness/wolo-gw.log"
	@echo "[restart] all three services launched. Use 'make stop' to stop them."

# 仅清理运行时缓存（__pycache__ / .pytest_cache / .mypy_cache / .ruff_cache），不停进程
clean-runtime:
	@$(RESTART_CLEAN) --no-stop

# 清理缓存和构建产物（传统 clean：不动运行时进程）
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build *.egg-info
