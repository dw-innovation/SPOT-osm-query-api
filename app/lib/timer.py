import time

"""
Simple timing utility for measuring elapsed time between checkpoints.

The `Timer` class allows tracking the time (in milliseconds) between named
checkpoints. Useful for lightweight performance logging or profiling of code
sections. Checkpoints are cumulative, not overlapping.
"""

class Timer:
    """A timer for measuring elapsed time between checkpoints in milliseconds.

    This class provides a way to track the time between named checkpoints in
    your code. It uses wall-clock time (`time.time()`) and stores durations
    in milliseconds.

    Example:
        timer = Timer()
        ...  # do something
        timer.add_checkpoint("load_data")
        ...  # do more
        timer.add_checkpoint("process")
        print(timer.get_all_checkpoints())  # {'load_data': ..., 'process': ...}
    """
    def __init__(self):
        """Initialize the timer and start the first checkpoint timer."""
        # Dictionary to store the checkpoints with their respective elapsed times in ms.
        self.checkpoints = {}
        self.last_checkpoint_time = time.time()

    def add_checkpoint(self, checkpoint_name):
        """Add a named checkpoint and record the time since the last one.

        Args:
            checkpoint_name (str): The name of the checkpoint to record.

        Returns:
            int: Elapsed time in milliseconds since the last checkpoint.
        """
        current_time = time.time()

        # Convert to milliseconds and round to the nearest integer
        elapsed_time = round((current_time - self.last_checkpoint_time) * 1000)
        self.last_checkpoint_time = current_time  # Update the last checkpoint time
        self.checkpoints[checkpoint_name] = elapsed_time
        return elapsed_time

    def get_checkpoint(self, checkpoint_name):
        """Get the elapsed time for a specific checkpoint.

        Args:
            checkpoint_name (str): The name of the checkpoint to retrieve.

        Returns:
            int | None: Elapsed time in milliseconds, or `None` if not found.
        """
        return self.checkpoints.get(checkpoint_name, None)

    def reset(self):
        """Reset the timer and clear all existing checkpoints."""
        self.last_checkpoint_time = time.time()
        self.checkpoints = {}

    def get_all_checkpoints(self):
        """Get all recorded checkpoints.

        Returns:
            dict[str, int]: Dictionary of {checkpoint_name: elapsed_time_ms}.
        """
        return self.checkpoints
