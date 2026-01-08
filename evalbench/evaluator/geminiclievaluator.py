from typing import Any, List
import datetime
from dataset.evalgeminicliinput import EvalGeminiCliRequest
import logging
import subprocess
import os
import json


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, yolo=True):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.yolo = yolo


class GeminiCliEvaluator:
    def __init__(
        self,
        config,
    ):
        self.config = config
        self.gemini_cli_version = config["gemini_cli_version"]

    def execute_cli_command(
        self, command: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, env=env
            )
            return result
        except FileNotFoundError:
            return subprocess.CompletedProcess(command, 127, "", f"Error: Command not found: {command[0]}")
        except Exception as e:
            return subprocess.CompletedProcess(command, 1, "", f"An unexpected error occurred: {e}")

    def run_gemini_cli(self, cli_cmd: CLICommand):
        gemini_settings_path = os.path.expanduser("~/.gemini/settings.json")
        if not os.path.exists(os.path.dirname(gemini_settings_path)):
            os.makedirs(os.path.dirname(gemini_settings_path))
        if not os.path.exists(gemini_settings_path):
            with open(gemini_settings_path, 'w') as f:
                json.dump({}, f)

        env = os.environ.copy()
        env.update(cli_cmd.env)
        env.update(
            {
                "GEMINI_CLI_SYSTEM_SETTINGS_PATH": gemini_settings_path,
            }
        )

        command = [
            "npx",
            "-y",
            cli_cmd.cli,
        ]
        if cli_cmd.resume:
            command.append("--resume")
        if cli_cmd.yolo:
            command.append("--yolo")

        command.extend([
            "--output-format",
            "json",
            "--prompt",
            cli_cmd.prompt,
        ])

        return self.execute_cli_command(
            command,
            env=env,
        )

    def evaluate(
        self,
        dataset: List[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        logging.info("Running Gemini CLI evaluation")

        for item in dataset:
            eval_set = json.loads(item.payload)
            for scenario in eval_set.get("scenarios", []):
                prompt = scenario["starting_prompt"]
                env = scenario.get("env", {})

                cli_cmd = CLICommand(
                    cli=self.gemini_cli_version,
                    prompt=prompt,
                    env=env,
                )
                result = self.run_gemini_cli(cli_cmd=cli_cmd)

                logging.info(f"Gemini CLI exit code: {result.returncode}")
                logging.info(f"Gemini CLI stdout: {result.stdout}")
                logging.info(f"Gemini CLI stderr: {result.stderr}")

                score = 0
                explanation = ""
                try:
                    output_json = json.loads(result.stdout)
                    executed_tools = []
                    if (
                        "stats" in output_json
                        and "tools" in output_json["stats"]
                        and "byName" in output_json["stats"]["tools"]
                    ):
                        executed_tools = list(
                            output_json["stats"]["tools"]["byName"].keys()
                        )

                    expected_trajectory = scenario.get("expected_trajectory", [])

                    if set(expected_trajectory).issubset(set(executed_tools)):
                        score = 1
                        explanation = "All expected tools were called."
                    else:
                        score = 0
                        explanation = f"Not all expected tools were called. Expected: {expected_trajectory}, Found: {executed_tools}"

                except json.JSONDecodeError:
                    score = 0
                    explanation = "Failed to parse Gemini CLI output as JSON."
                except Exception as e:
                    score = 0
                    explanation = f"An error occurred during scoring: {e}"

                eval_outputs.append({
                    "eval_id": scenario["id"],
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                    "prompt_generator_error": None,
                    "generated_error": None,
                    "sql_generator_error": None,
                    "golden_error": None,
                })
                scoring_results.append({
                    "eval_id": scenario["id"],
                    "score": score,
                    "explanation": explanation
                })

        return eval_outputs, scoring_results
