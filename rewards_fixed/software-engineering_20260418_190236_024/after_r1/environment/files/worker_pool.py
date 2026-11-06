#!/usr/bin/env python3
"""
Distributed Task Processing System with Heartbeat Monitoring

This module implements a worker pool that processes tasks in parallel using
multiprocessing. Each worker sends periodic heartbeats, and a supervisor
monitors workers to detect and restart any that become unresponsive.
"""

import multiprocessing
import time
import sys
import random
from typing import Callable, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import traceback


class WorkerState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"


def _worker_process(worker_id: int, heartbeat_event: multiprocessing.Event,
                    shutdown_event: multiprocessing.Event, 
                    task_queue: multiprocessing.Queue,
                    result_queue: multiprocessing.Queue, 
                    heartbeat_interval: float):
    """Worker process main function - execute tasks and send heartbeats."""
    print(f"[Worker {worker_id}] Starting", flush=True)
    
    while not shutdown_event.is_set():
        # Send heartbeat pulse
        heartbeat_event.set()
        heartbeat_event.clear()
        
        try:
            # Try to get a task with timeout
            task = task_queue.get(timeout=heartbeat_interval)
            if task is None:  # Poison pill
                break
            
            func, args = task
            try:
                result = func(*args)
                result_queue.put((worker_id, result, None))
            except Exception as e:
                result_queue.put((worker_id, None, str(e)))
                
        except Exception:
            # Queue timeout - no task available, just continue heartbeat loop
            pass
    
    print(f"[Worker {worker_id}] Shutting down", flush=True)


def sample_task(x: int) -> int:
    """A simple CPU-bound task for testing."""
    result = x * x
    time.sleep(random.uniform(0.001, 0.01))
    return result


class WorkerPool:
    """
    A pool of worker processes with heartbeat monitoring.
    
    Workers periodically pulse their heartbeat events, and the supervisor
    checks that each worker is still alive by waiting on the heartbeat event
    with a timeout.
    """
    
    def __init__(self, num_workers: int = 4, heartbeat_timeout: float = 0.5,
                 max_restarts: int = 3, heartbeat_interval: float = 0.01):
        self.num_workers = num_workers
        self.heartbeat_timeout = heartbeat_timeout
        self.max_restarts = max_restarts
        self.heartbeat_interval = heartbeat_interval
        
        # Shared queues for task distribution and result collection
        self.task_queue = multiprocessing.Queue()
        self.result_queue = multiprocessing.Queue()
        
        # Worker tracking - using lists indexed by worker_id
        self.processes: List[Optional[multiprocessing.Process]] = []
        self.heartbeat_events: List[multiprocessing.Event] = []
        self.shutdown_events: List[multiprocessing.Event] = []
        self.restart_counts: List[int] = []
        self.states: List[WorkerState] = []
        
        self.running = False
        self.supervisor_process: Optional[multiprocessing.Process] = None
        
        # Events for supervisor control
        self.supervisor_shutdown = multiprocessing.Event()
        
        # Statistics
        self.false_restart_count = multiprocessing.Value('i', 0)
    
    def _create_worker(self, worker_id: int):
        """Create a new worker process."""
        heartbeat_event = multiprocessing.Event()
        shutdown_event = multiprocessing.Event()
        
        process = multiprocessing.Process(
            target=_worker_process,
            args=(worker_id, heartbeat_event, shutdown_event,
                  self.task_queue, self.result_queue, self.heartbeat_interval)
        )
        process.start()
        
        # Store in lists
        while len(self.processes) <= worker_id:
            self.processes.append(None)
            self.heartbeat_events.append(None)
            self.shutdown_events.append(None)
            self.restart_counts.append(0)
            self.states.append(WorkerState.IDLE)
        
        self.processes[worker_id] = process
        self.heartbeat_events[worker_id] = heartbeat_event
        self.shutdown_events[worker_id] = shutdown_event
        self.states[worker_id] = WorkerState.RUNNING
    
    def _supervisor_loop(self, workers_data):
        """Supervisor loop - monitors worker heartbeats."""
        # Unpack the shared data
        heartbeat_events = workers_data['heartbeat_events']
        shutdown_events = workers_data['shutdown_events']
        states = workers_data['states']
        restart_counts = workers_data['restart_counts']
        task_queue = workers_data['task_queue']
        result_queue = workers_data['result_queue']
        heartbeat_interval = workers_data['heartbeat_interval']
        heartbeat_timeout = workers_data['heartbeat_timeout']
        max_restarts = workers_data['max_restarts']
        supervisor_shutdown = workers_data['supervisor_shutdown']
        false_restart_count = workers_data['false_restart_count']
        
        num_workers = len(heartbeat_events)
        
        print("[Supervisor] Starting heartbeat monitoring", flush=True)
        
        while not supervisor_shutdown.is_set():
            for worker_id in range(num_workers):
                if states[worker_id].value != "running":
                    continue
                
                # Check heartbeat with timeout
                # This should return True if the worker is alive and pulsing
                alive = heartbeat_events[worker_id].wait(heartbeat_timeout)
                
                if not alive:
                    # Worker appears dead - log and mark for restart
                    print(f"[Supervisor] Worker {worker_id} heartbeat timeout - "
                          f"marking as dead (restart #{restart_counts[worker_id] + 1})", flush=True)
                    
                    # Increment false restart counter
                    with false_restart_count.get_lock():
                        false_restart_count.value += 1
                    
                    # Signal worker to shutdown
                    shutdown_events[worker_id].set()
                    
                    states[worker_id] = WorkerState.STOPPED
                    
                    # Restart if within limits
                    if restart_counts[worker_id] < max_restarts:
                        # Create new worker
                        heartbeat_event = multiprocessing.Event()
                        shutdown_event = multiprocessing.Event()
                        
                        process = multiprocessing.Process(
                            target=_worker_process,
                            args=(worker_id, heartbeat_event, shutdown_event,
                                  task_queue, result_queue, heartbeat_interval)
                        )
                        process.start()
                        
                        heartbeat_events[worker_id] = heartbeat_event
                        shutdown_events[worker_id] = shutdown_event
                        restart_counts[worker_id] = restart_counts[worker_id] + 1
                        states[worker_id] = WorkerState.RUNNING
                    else:
                        print(f"[Supervisor] Worker {worker_id} exceeded max restarts", flush=True)
            
            # Small delay between checks
            time.sleep(0.01)
        
        print("[Supervisor] Shutting down", flush=True)
    
    def start(self):
        """Start the worker pool."""
        if self.running:
            return
        
        # Create initial workers
        for i in range(self.num_workers):
            self._create_worker(i)
        
        # Prepare data for supervisor (needs to be picklable)
        workers_data = {
            'heartbeat_events': self.heartbeat_events,
            'shutdown_events': self.shutdown_events,
            'states': self.states,
            'restart_counts': self.restart_counts,
            'task_queue': self.task_queue,
            'result_queue': self.result_queue,
            'heartbeat_interval': self.heartbeat_interval,
            'heartbeat_timeout': self.heartbeat_timeout,
            'max_restarts': self.max_restarts,
            'supervisor_shutdown': self.supervisor_shutdown,
            'false_restart_count': self.false_restart_count,
        }
        
        # Start supervisor
        self.supervisor_process = multiprocessing.Process(
            target=self._supervisor_loop,
            args=(workers_data,)
        )
        self.supervisor_process.start()
        
        self.running = True
        print(f"[Pool] Started {self.num_workers} workers", flush=True)
    
    def submit(self, func: Callable, *args):
        """Submit a task for execution."""
        self.task_queue.put((func, args))
    
    def map(self, func: Callable, items: List[Any]) -> List[Any]:
        """Apply a function to all items and return results."""
        n = len(items)
        for item in items:
            self.submit(func, item)
        
        results = []
        for _ in range(n):
            worker_id, result, error = self.result_queue.get()
            if error:
                print(f"[Pool] Task from worker {worker_id} failed: {error}", flush=True)
            results.append(result)
        
        return results
    
    def shutdown(self):
        """Shutdown the worker pool."""
        if not self.running:
            return
        
        # Signal supervisor to stop
        self.supervisor_shutdown.set()
        
        # Signal all workers to stop
        for shutdown_event in self.shutdown_events:
            if shutdown_event:
                shutdown_event.set()
        
        # Send poison pills
        for _ in range(self.num_workers):
            self.task_queue.put(None)
        
        # Wait for workers
        for process in self.processes:
            if process:
                process.join(timeout=1.0)
                if process.is_alive():
                    process.terminate()
        
        # Wait for supervisor
        if self.supervisor_process:
            self.supervisor_process.join(timeout=2.0)
            if self.supervisor_process.is_alive():
                self.supervisor_process.terminate()
        
        self.running = False
        
        # Report statistics
        false_restarts = self.false_restart_count.value
        print(f"\n[Pool] Shutdown complete", flush=True)
        print(f"[Pool] False restarts (workers incorrectly marked dead): {false_restarts}", flush=True)
        
        return false_restarts


def main():
    """Test the worker pool with a sample workload."""
    print("=" * 60, flush=True)
    print("Worker Pool Heartbeat Test", flush=True)
    print("=" * 60, flush=True)
    
    pool = WorkerPool(num_workers=4, heartbeat_timeout=0.5, max_restarts=10)
    pool.start()
    
    # Give workers time to start
    time.sleep(0.1)
    
    # Submit a batch of tasks
    items = list(range(100))
    print(f"\n[Main] Submitting {len(items)} tasks...", flush=True)
    
    start_time = time.time()
    results = pool.map(sample_task, items)
    elapsed = time.time() - start_time
    
    print(f"[Main] Completed {len(results)} tasks in {elapsed:.2f}s", flush=True)
    
    # Keep running for a bit to see heartbeat monitoring in action
    print("\n[Main] Running extended heartbeat monitoring test...", flush=True)
    time.sleep(2.0)
    
    # Shutdown and get false restart count
    false_restarts = pool.shutdown()
    
    print("\n" + "=" * 60, flush=True)
    if false_restarts > 0:
        print(f"FAILURE: {false_restarts} workers were incorrectly marked as dead", flush=True)
        print("This indicates a bug in the heartbeat detection mechanism.", flush=True)
        print("=" * 60, flush=True)
        sys.exit(1)
    else:
        print("SUCCESS: All workers maintained valid heartbeats", flush=True)
        print("=" * 60, flush=True)
        sys.exit(0)


if __name__ == "__main__":
    # Use fork method for better compatibility
    multiprocessing.set_start_method('fork', force=True)
    main()
