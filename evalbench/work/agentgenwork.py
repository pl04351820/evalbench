"""AgentGenWork class."""

from typing import Any, List, Dict
import json
import logging
import subprocess

from work.work import Work
from work.agentscorework import AgentScoreWork
from generators.models.gemini_cli import GeminiCliGenerator, CLICommand


class AgentGenWork(Work):
    """Work class for running agent generation and scoring."""

    def __init__(
        self,
        generator: GeminiCliGenerator,
        agent_version: str,
        eval_result: Any,
        job_id: str = "",
        metadata: dict = None,
        simulated_user: Any = None
    ):
        self.generator = generator
        self.agent_version = agent_version
        self.eval_result = eval_result
        self.job_id = job_id
        self.metadata = metadata or {}
        self.simulated_user = simulated_user

    def run(self, work_config: Any = None) -> Any:
        """Runs the agent generation and scoring work.

        Args:
            work_config: Optional configuration for the work.

        Returns:
            The updated eval_result with results and scores.
        """
        eval_result = self.eval_result
        if not hasattr(eval_result, "agent_results"):
            eval_result.agent_results = []
        if not hasattr(eval_result, "scoring_results"):
            eval_result.scoring_results = []

        try:
            eval_set = json.loads(eval_result.payload)
            for scenario in eval_set.get("scenarios", []):
                self._process_scenario(scenario, eval_result)
        except Exception as e:
            logging.error(f"Error processing item: {e}")

        return eval_result

    def _process_scenario(self, scenario: Dict[str, Any], eval_result: Any):
        """Processes a single scenario."""
        current_prompt = scenario["starting_prompt"]
        env = scenario.get("env", {})
        max_turns = scenario.get("max_turns", 1)
        conversation_plan = scenario.get("conversation_plan", "")
        conversation_history = []
        accumulated_tools = []
        last_result = None

        for turn in range(max_turns):
            logging.info(f"Turn {turn + 1}/{max_turns} - Prompt: {current_prompt}")
            
            if isinstance(self.generator, GeminiCliGenerator):
                cli_cmd = self.generator.create_command(
                    cli=self.agent_version,
                    prompt=current_prompt,
                    env=env,
                    resume=(turn > 0)
                )
                result = self.generator.safe_generate(cli_cmd)
            else:
                result = self.generator.generate(current_prompt)
            
            last_result = result

            self._log_cli_result(turn, max_turns, result)

            tools = []
            if isinstance(self.generator, GeminiCliGenerator):
                 tools = self.generator.extract_tools(result.stdout)
            accumulated_tools.extend(tools)

            conversation_history.append({
                "user": current_prompt,
                "agent": result.stdout
            })

            if turn < max_turns - 1:
                if self.simulated_user:
                    next_response = self.simulated_user.get_next_response(
                        conversation_plan,
                        conversation_history,
                        result.stdout
                    )
                    if "TERMINATE" in next_response:
                        logging.info("Simulated user terminated conversation.")
                        break
                    current_prompt = next_response
                else:
                    break

        if last_result:
            self._finalize_scenario(
                scenario, 
                last_result, 
                conversation_history, 
                accumulated_tools, 
                eval_result
            )

    def _log_cli_result(self, turn: int, max_turns: int, result: subprocess.CompletedProcess):
        logging.info(f"Turn {turn + 1}/{max_turns} - Gemini CLI exit code: {result.returncode}")
        logging.info(f"Turn {turn + 1}/{max_turns} - Gemini CLI stdout: {result.stdout}")
        logging.info(f"Turn {turn + 1}/{max_turns} - Gemini CLI stderr: {result.stderr}")

    def _finalize_scenario(
        self,
        scenario: Dict[str, Any],
        last_result: subprocess.CompletedProcess,
        conversation_history: List[Dict[str, str]],
        accumulated_tools: List[str],
        eval_result: Any
    ):
        """Finalizes the scenario by scoring and appending results."""
        # Prepare intermediate eval_output with all necessary data for scoring
        eval_output_data = {
            "eval_id": scenario["id"],
            "stdout": last_result.stdout,
            "stderr": last_result.stderr,
            "returncode": last_result.returncode,
            "prompt_generator_error": None,
            "generated_error": None,
            "sql_generator_error": None,
            "golden_error": None,
            "generated_sql": "skipped",
            "prompt": scenario["starting_prompt"],
            "conversation_history": json.dumps(conversation_history, indent=2),
            "scenario": scenario,
            "accumulated_tools": accumulated_tools,
            "job_id": self.job_id,
            "metadata": self.metadata
        }

        score_work = AgentScoreWork(
            config=self.metadata,
            eval_output=eval_output_data,
            scoring_results=eval_result.scoring_results
        )
        score_work.run()

        eval_result.agent_results.append(eval_output_data)
