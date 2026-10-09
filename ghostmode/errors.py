class GhostModeError(Exception):
    pass

class GuardViolation(GhostModeError):
    pass

class OllamaUnreachable(GhostModeError):
    pass

class ModelMissing(GhostModeError):
    pass

class GenerationTimeout(GhostModeError):
    pass

class CircuitOpen(GhostModeError):
    pass

class PatchError(GhostModeError):
    pass
