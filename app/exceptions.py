class VoteRejectException(Exception):
    """
    Dedicated exception for vote processing to simplify the logic.
    """
    def __init__(self, message):
        super().__init__(message)
