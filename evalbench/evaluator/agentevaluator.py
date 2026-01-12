from typing import Any, List
import datetime
from dataset.evalgeminicliinput import EvalGeminiCliRequest
import logging
import subprocess
import json
from generators.models.gemini_cli import GeminiCliGenerator, CLICommand
from util.config import load_yaml_config


class AgentEvaluator:
    def __init__(
        self,
        config,
    ):
        self.config = config
        
        # Load model config if provided
        model_config = config
        if "model_config" in config and isinstance(config["model_config"], str):
            loaded_config = load_yaml_config(config["model_config"])
            # Merge main config into loaded config, giving precedence to main config
            model_config = loaded_config.copy()
            model_config.update(config)

        self.agent_version = model_config.get("gemini_cli_version", config.get("gemini_cli_version"))
        
        generator_type = model_config.get("generator")
        if generator_type == "gemini_cli":
            self.generator = GeminiCliGenerator(model_config)
        else:
            raise ValueError(f"Unsupported generator type for AgentEvaluator: {generator_type}")

    def run_gemini_cli(self, cli_cmd: CLICommand) -> subprocess.CompletedProcess:
        result = self.generator.generate(cli_cmd)
        if isinstance(result, str) and not result:
            return subprocess.CompletedProcess(
                args=cli_cmd.cli,
                returncode=1,
                stdout="",
                stderr="Error: Generator returned empty response (possibly resource exhausted).",
            )
        return result

    def evaluate(
        self,
        dataset: List[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        if isinstance(self.generator, GeminiCliGenerator):
            return self._evaluate_gemini_cli(dataset, job_id, run_time)
        else:
            raise NotImplementedError("This evaluator currently only supports GeminiCliGenerator")

    def _evaluate_gemini_cli(
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
                    cli=self.agent_version,
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
