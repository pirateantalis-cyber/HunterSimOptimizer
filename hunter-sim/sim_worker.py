"""
Simulation worker process - runs Rust simulations without GUI interference.
Communicates via multiprocessing Queue to bypass tkinter mainloop contention.
"""
import multiprocessing as mp
import sys
import traceback


def worker_process(task_queue, result_queue):
    """
    Worker process that runs Rust simulations.
    Runs in isolated process with no tkinter dependencies.
    """
    # Import rust_sim here (after process fork/spawn)
    try:
        import rust_sim
    except ImportError as e:
        result_queue.put((0, None, f"Worker failed to import rust_sim: {e}"))
        return
    
    while True:
        try:
            task = task_queue.get()
            if task is None:  # Poison pill
                break
            
            task_id, config, num_sims = task
            
            # Run Rust simulation
            result = rust_sim.simulate(
                hunter=config["hunter_name"],
                level=config.get("level", 100),
                stats=config.get("stats", {}),
                talents=config["talents"],
                attributes=config["attributes"],
                inscryptions=config.get("inscryptions", {}),
                mods=config.get("mods", {}),
                relics=config.get("relics", {}),
                gems=config.get("gems", {}),
                gadgets=config.get("gadgets", {}),
                bonuses=config.get("bonuses", {}),
                num_sims=num_sims,
                parallel=True  # Full parallelism in isolated process
            )
            
            result_queue.put((task_id, result, None))
            
        except Exception as e:
            tb = traceback.format_exc()
            result_queue.put((task_id, None, f"{e}\n{tb}"))


class SimulationWorker:
    """Manages a worker process for running simulations."""
    
    def __init__(self):
        self.task_queue = mp.Queue()
        self.result_queue = mp.Queue()
        self.process = mp.Process(
            target=worker_process,
            args=(self.task_queue, self.result_queue),
            daemon=False  # Ensure clean shutdown
        )
        self.next_task_id = 0
        self.pending_tasks = {}  # task_id -> metadata
        self.process.start()
    
    def submit(self, config, num_sims, metadata=None):
        """Submit a simulation task. Returns task_id."""
        task_id = self.next_task_id
        self.next_task_id += 1
        self.task_queue.put((task_id, config, num_sims))
        self.pending_tasks[task_id] = metadata
        return task_id
    
    def get_result(self, block=False, timeout=None):
        """
        Get a completed result if available.
        Returns: (task_id, result, error, metadata) or None if no results ready.
        """
        if not block and self.result_queue.empty():
            return None
        
        try:
            task_id, result, error = self.result_queue.get(block=block, timeout=timeout)
            metadata = self.pending_tasks.pop(task_id, None)
            return (task_id, result, error, metadata)
        except:
            return None
    
    def shutdown(self):
        """Gracefully shutdown worker process."""
        self.task_queue.put(None)  # Poison pill
        self.process.join(timeout=2)
        if self.process.is_alive():
            self.process.terminate()
