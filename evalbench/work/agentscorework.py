"""AgentScoreWork class."""

from typing import Any, List, Dict, Tuple
import logging

from work.work import Work
from scorers.trajectorymatcher import TrajectoryMatcher


class AgentScoreWork(Work):
    """Work class for scoring agent generation results."""

    def __init__(
        self,
        config: dict,
        eval_output: dict,
        scoring_results: list,
        global_models: Any = None,
    ):
        self.config = config
        self.eval_output = eval_output
        self.scoring_results = scoring_results
        self.global_models = global_models

    def run(self, work_config: Any = None) -> Any:
        """Runs the agent scoring work.

        Args:
            work_config: Optional configuration for the work.

        Returns:
            The scoring result dictionary.
        """
        score, explanation = self._score_result()

        scoring_result_data = {
            "id": self.eval_output.get("eval_id"),
            "eval_id": self.eval_output.get("eval_id"),
            "score": score,
            "explanation": explanation,
            "comparator": "trajectory_matcher",
            "generated_sql": "skipped",
            "generated_error": None,
            "job_id": self.eval_output.get("job_id"),
            "database": self.eval_output.get("metadata", {}).get("database", "unknown"),
            "dialects": self.eval_output.get("metadata", {}).get("dialects", []),
        }

        self.scoring_results.append(scoring_result_data)
        return self.eval_output

    def _score_result(self) -> Tuple[int, str]:
        score = 0
        explanation = ""
        try:
            executed_tools = self.eval_output.get("accumulated_tools", [])
            expected_trajectory = self.eval_output.get("scenario", {}).get("expected_trajectory", [])
            starting_prompt = self.eval_output.get("scenario", {}).get("starting_prompt", "")

            metadata = self.eval_output.get("metadata", {})
            scorer_config = metadata.get("scorers", {}).get("trajectory_matcher", {})
            
            matcher = TrajectoryMatcher(scorer_config)
            score, explanation = matcher.compare(
                nl_prompt=starting_prompt,
                golden_query="",
                query_type="",
                golden_execution_result=expected_trajectory,
                golden_eval_result="",
                golden_error="",
                generated_query="",
                generated_execution_result=executed_tools,
                generated_eval_result="",
                generated_error=None
            )

        except Exception as e:
            score = 0
            explanation = f"An error occurred during scoring: {e}"
            logging.error(explanation)

        return score, explanation
