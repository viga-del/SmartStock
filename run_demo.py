"""
Main Launcher for the Supply Chain Order Promising & ATP Engine.

Usage:
  python run_demo.py --cli    (Run interactive terminal scenarios)
  python run_demo.py --web    (Launch Web Dashboard & REST API on http://localhost:8000)
  python run_demo.py --test   (Run Pytest verification suite)
"""

import argparse
import os
import subprocess
import sys


def run_tests():
    print("\n--- Running Automated Test Suite ---")
    cmd = [sys.executable, "-m", "pytest", "tests/test_engine.py", "-v"]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
    subprocess.run(cmd, env=env)


def run_cli_scenarios():
    import scenarios
    scenarios.console.print("\n[bold underline yellow]=== RUNNING SUPPLY CHAIN ORDER PROMISING DEMO SCENARIOS ===[/bold underline yellow]\n")
    scenarios.run_case_1_last_unit_competition()
    scenarios.run_case_2_future_production()
    scenarios.run_case_3_split_shipment_control()
    scenarios.console.print("[bold green]=== ALL DEMONSTRATION CASES COMPLETED SUCCESSFULLY! ===[/bold green]\n")


def run_web_dashboard(port: int = 8000):
    import uvicorn
    print(f"\n>>> Launching Supply Chain Web Dashboard at: http://localhost:{port}")
    print(f">>> OpenAPI Swagger Documentation at: http://localhost:{port}/docs\n")
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)


def main():
    parser = argparse.ArgumentParser(description="Supply Chain Order Promising & ATP Engine")
    parser.add_argument("--cli", action="store_true", help="Run rich CLI demonstration scenarios")
    parser.add_argument("--web", action="store_true", help="Launch interactive Web Dashboard")
    parser.add_argument("--test", action="store_true", help="Run automated test suite")
    parser.add_argument("--port", type=int, default=8000, help="Port for web dashboard (default: 8000)")

    args = parser.parse_args()

    if args.test:
        run_tests()
    elif args.web:
        run_web_dashboard(port=args.port)
    elif args.cli:
        run_cli_scenarios()
    else:
        # Default behavior: run CLI scenarios and advise how to launch web
        run_cli_scenarios()
        print("\n[TIP] To launch the interactive browser dashboard, run:")
        print("    python run_demo.py --web")
        print("    Then open http://localhost:8000 in your browser!\n")


if __name__ == "__main__":
    main()
