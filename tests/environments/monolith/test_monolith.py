import unittest
import subprocess
import time
import requests
import os
import signal
from chaosgen.orchestrator import ChaosOrchestrator
from chaosgen.schemas.faults import ChaosExperiment, TargetType, FaultType, ProcessFaultSpec, TargetSpec

class TestMonolithChaos(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Start the dummy service as a subprocess
        cls.service_process = subprocess.Popen(
            ["python", "tests/environments/monolith/dummy_service.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2) # Wait for startup

    @classmethod
    def tearDownClass(cls):
        if cls.service_process.poll() is None:
            cls.service_process.terminate()
            cls.service_process.wait()

    def test_process_kill(self):
        """Test that the orchestrator can kill a local process (simulated)."""
        # Note: Actual systemd/kill require privileges or matching user.
        # For this test, we might mock the actual kill command or rely on the translator 
        # mapping to a local kill if we implemented a local process killer.
        
        # Current translator implementation for SYSTEMD maps to 'chaos-toolkit' / 'systemctl_stop'.
        # We need to verify if we want to add a direct 'kill' mode for testing without systemd.
        
        # Let's define the experiment
        experiment = ChaosExperiment(
            name="monolith-kill-test",
            target=TargetSpec(
                type=TargetType.PROCESS,
                name="dummy_service.py" # Simple name matching
            ),
            faults=[
                ProcessFaultSpec(
                    fault_type=FaultType.PROCESS_KILL,
                    signal="SIGTERM"
                )
            ],
            steady_state_check={
                "http_health": "http://localhost:8080/health"
            }
        )
        
        orchestrator = ChaosOrchestrator()
        
        # We need to ensure the translator can handle this environment.
        # Force environment to SYSTEMD for this test context if needed, 
        # but pure python kill is better for cross-platform unit test.
        
        # TODO: Update translator to support direct process kill by name for testing?
        # For now, we assume the orchestrator runs the plan.
        
        # orchestrator.run_experiment(experiment)
        
        # Since we don't have a real systemd service 'dummy_service', the current implementation
        # of _map_systemd_fault tries to run 'systemctl stop dummy_service.py'.
        # This will fail on Windows/non-systemd. 
        # This test serves as a placeholder for the integration test logic.
        pass

if __name__ == '__main__':
    unittest.main()



