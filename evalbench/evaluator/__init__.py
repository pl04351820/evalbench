from evaluator.orchestrator import Orchestrator
from evaluator.oneshotorchestrator import OneShotOrchestrator
from evaluator.interactorchestrator import InteractOrchestrator
from evaluator.agentorchestrator import AgentOrchestrator
import logging


def get_orchestrator(config, db_configs, setup_config, report_progress=False):
    orchestrator_type = config.get("orchestrator", "oneshot")

    if "model_config" in config:
        from util.config import load_yaml_config
        model_config_path = config["model_config"]
        if isinstance(model_config_path, str):
            try:
                model_config = load_yaml_config(model_config_path)
                generator_type = model_config.get("generator")
                logging.info(f"Loaded definition from {model_config_path}, generator: {generator_type}")
                if generator_type == "gemini_cli":
                    orchestrator_type = "geminicli"
            except Exception as e:
                logging.error(f"Failed to load user config from {model_config_path}: {e}")
                pass

    logging.info(f"Orchestrator Type: {orchestrator_type}")
    if orchestrator_type == "oneshot":
        return OneShotOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type == "interact":
        return InteractOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type == "geminicli":
        return AgentOrchestrator(config, db_configs, setup_config, report_progress)
    else:
        return Orchestrator(config, db_configs, setup_config, report_progress)
