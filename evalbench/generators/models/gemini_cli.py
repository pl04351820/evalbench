from .generator import QueryGenerator
import subprocess
import os
import json
import logging
import re
import shutil


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, yolo=True):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.yolo = yolo


class GeminiCliGenerator(QueryGenerator):
    """Generator queries using Gemini CLI."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "gemini_cli"
        self.gemini_cli_version = querygenerator_config.get("gemini_cli_version", "gemini-cli")
        self.setup_config = querygenerator_config.get("setup", {})
        if self.setup_config:
            self._setup()

    def _setup(self):
        """Performs initial setup for Gemini CLI."""
        gemini_settings_path = os.path.expanduser("~/.gemini/settings.json")

        if not os.path.exists(os.path.dirname(gemini_settings_path)):
            os.makedirs(os.path.dirname(gemini_settings_path))

        # Setup MCP Servers
        if "mcp_servers" in self.setup_config:
            self._setup_mcp_servers(self.setup_config["mcp_servers"], gemini_settings_path)

        # Install Extensions
        if "extensions" in self.setup_config:
            self._install_extensions(self.setup_config["extensions"])

    def _setup_mcp_servers(self, mcp_servers_config: dict, settings_path: str):
        """Configures MCP servers in the settings file."""
        current_settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    current_settings = json.load(f)
            except json.JSONDecodeError:
                pass

        if "mcpServers" not in current_settings:
            current_settings["mcpServers"] = {}

        # Merge/Overwrite configurations
        for server_name, config in mcp_servers_config.items():
            if "command" not in config:
                package_name = config.get("package", server_name)

                config["command"] = "npm"
                args = config.get("args", [])

                config["args"] = ["exec", "--yes", package_name, "--"] + args

            current_settings["mcpServers"][server_name] = config

        with open(settings_path, 'w') as f:
            json.dump(current_settings, f, indent=2)

    def _install_extensions(self, extensions: list[str]):
        """Installs/Syncs specified extensions using gemini-cli."""
        extensions = sorted(list(set(extensions)))

        installed_extensions = set()
        try:
            cmd = ["npm", "exec", "--yes", self.gemini_cli_version, "--", "extensions", "list"]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )

            for line in result.stdout.splitlines():
                line = line.strip()

                warn_match = re.search(r"Warning: Skipping extension in (.*?): Configuration file not found", line)
                if warn_match:
                    corrupted_path = warn_match.group(1).strip()
                    logging.warning(f"Detected corrupted extension at {corrupted_path}. Removing...")
                    try:
                        shutil.rmtree(corrupted_path)
                    except Exception as e:
                        logging.error(f"Failed to remove corrupted extension directory {corrupted_path}: {e}")
                    continue

                keychain_match = re.search(r"Warning: Skipping extension in (.*?): Keychain is not available", line)
                if keychain_match:
                    ext_path = keychain_match.group(1).strip()
                    manifest_path = os.path.join(ext_path, "gemini-extension.json")
                    logging.warning(f"Detected keychain availability issue for {ext_path}. Patching manifest for headless compatibility.")
                    try:
                        if os.path.exists(manifest_path):
                            with open(manifest_path, 'r') as f:
                                manifest_content = f.read()

                            manifest_content = manifest_content.replace('"sensitive": true', '"sensitive": false')
                            with open(manifest_path, 'w') as f:
                                f.write(manifest_content)
                            logging.info(f"Successfully patched {manifest_path} to bypass keychain requirements.")

                            name_match = re.search(r"extensions/([^/]+)$", ext_path)
                            if name_match:
                                installed_extensions.add(name_match.group(1))
                    except Exception as e:
                        logging.error(f"Failed to patch manifest for headless compatibility at {manifest_path}: {e}")
                    continue

                if not line or line.startswith("Source:") or line.startswith("Path:") or line.startswith("ID:") or line.startswith("name:"):
                    continue

                if "✓" in line or ("(" in line and ")" in line):
                    parts = line.split()
                    if len(parts) >= 2:
                        if "(" in parts[0]:
                            continue

                        name = parts[1] if "✓" in parts[0] and len(parts) >= 2 else parts[0]

                        if not name.startswith("("):
                            installed_extensions.add(name)

        except Exception as e:
            logging.warning(f"Failed to list extensions: {e}")

        # Uninstall extraneous extensions
        to_uninstall = []
        for ext_name in installed_extensions:
            keep = False
            for req in extensions:
                if ext_name in req:
                    keep = True
                    break

            if not keep:
                to_uninstall.append(ext_name)

        if to_uninstall:
            logging.info(f"Uninstalling extraneous extensions: {to_uninstall}")
            for ext in to_uninstall:
                try:
                    subprocess.run(
                        ["npm", "exec", "--yes", self.gemini_cli_version, "--", "extensions", "uninstall", ext],
                        check=False,
                        capture_output=True
                    )
                except Exception as e:
                    logging.warning(f"Failed to uninstall extension {ext}: {e}")

        # Install requested extensions
        for ext in extensions:
            already_installed = False
            for installed in installed_extensions:
                if installed == ext:
                    already_installed = True
                    break
                if "/" in ext and ext.rstrip("/").rstrip(".git").endswith(installed):
                    already_installed = True
                    break

            if already_installed:
                logging.info(f"Extension '{ext}' appears to be already installed. Skipping.")
                continue

            logging.info(f"Installing extension: {ext}")
            try:
                # gemini extensions install <name_or_url> --consent
                result = subprocess.run(
                    ["npm", "exec", "--yes", self.gemini_cli_version, "--", "extensions", "install", ext, "--consent"],
                    check=False,
                    capture_output=True,
                    text=True,
                    input="\n" * 10,
                    timeout=300
                )

                if result.returncode == 0:
                    logging.info(f"Successfully installed extension: {ext}")
                else:
                    logging.error(f"Failed to install extension {ext}. Return code: {result.returncode}, Output: {result.stdout}, Error: {result.stderr}")

                ext_name_match = re.search(r"([^/]+?)(?:\.git)?$", ext)
                if ext_name_match:
                    search_name = ext_name_match.group(1)
                    extensions_dir = os.path.expanduser("~/.gemini/extensions")
                    if os.path.exists(extensions_dir):
                        for item in os.listdir(extensions_dir):
                            if search_name in item:
                                manifest_path = os.path.join(extensions_dir, item, "gemini-extension.json")
                                if os.path.exists(manifest_path):
                                    with open(manifest_path, 'r') as f:
                                        manifest_content = f.read()
                                    if '"sensitive": true' in manifest_content:
                                        logging.warning(f"Newly installed extension {item} requires keychain. Patching manifest.")
                                        manifest_content = manifest_content.replace('"sensitive": true', '"sensitive": false')
                                        with open(manifest_path, 'w') as f:
                                            f.write(manifest_content)
                                        logging.info(f"Successfully patched {manifest_path}")
            except subprocess.TimeoutExpired:
                logging.error(f"Installation of extension {ext} timed out after 300 seconds.")
            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to install extension {ext}: {e.stderr}")

    def generate_internal(self, cli_cmd: CLICommand):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.gemini_cli_version, str(cli_cmd))

        return self._run_gemini_cli(cli_cmd)

    def _execute_cli_command(
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

    def _run_gemini_cli(self, cli_cmd: CLICommand):
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
            "npm",
            "exec",
            "--yes",
            cli_cmd.cli,
            "--",
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

        return self._execute_cli_command(
            command,
            env=env,
        )

    def parse_response(self, stdout: str) -> dict:
        """Parses the JSON output from Gemini CLI."""
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {}

    def extract_tools(self, stdout: str) -> list[str]:
        """Extracts the list of tools used from the CLI output."""
        output_json = self.parse_response(stdout)
        if (
            "stats" in output_json
            and "tools" in output_json["stats"]
            and "byName" in output_json["stats"]["tools"]
        ):
            return list(output_json["stats"]["tools"]["byName"].keys())
        return []

    def safe_generate(self, cli_cmd: CLICommand) -> subprocess.CompletedProcess:
        """Runs the generation and handles empty responses."""
        result = self.generate(cli_cmd)
        if isinstance(result, str) and not result:
            return subprocess.CompletedProcess(
                args=[cli_cmd.cli],
                returncode=1,
                stdout="",
                stderr="Error: Generator returned empty response (possibly resource exhausted).",
            )
        return result

    def create_command(self, cli: str, prompt: str, env: dict = None, resume: bool = False) -> CLICommand:
        """Creates a CLICommand object."""
        return CLICommand(cli=cli, prompt=prompt, env=env, resume=resume)
