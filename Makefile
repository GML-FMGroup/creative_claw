SHELL:=/bin/bash
.PHONY: cli_client
.PHONY: feishu_bot
.PHONY: telegram_bot
.PHONY: clean

cli_client:
	source ./.venv/bin/activate && \
	python ./apps/art_cli.py

telegram_bot:
	source ./.venv/bin/activate && \
	python ./apps/run_telegram.py

feishu_bot:
	source ./.venv/bin/activate && \
	python ./apps/run_feishu.py

clean:
	# rm ./outputs/* -r
	find . -type d -name "__pycache__" -exec rm -r {} +
