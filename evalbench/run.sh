#!/bin/bash

if [[ -z "${EVAL_CONFIG}" ]];
  then echo "EVAL_CONFIG is required";
  exit 0;
fi

# Refresh NPM Auth Tokens automatically
echo "Refreshing NPM authentication..."
"$PWD/evalbench/setup_npm_auth.sh"

# increase limit for number of open files
ulimit -n 4096

export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
python3 evalbench/evalbench.py   --experiment_config="$EVAL_CONFIG"