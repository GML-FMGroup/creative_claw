"""Compatibility alias for the renamed speech recognition expert."""

from src.agents.experts.speech_recognition.speech_recognition_expert import SpeechRecognitionExpert


class SpeechTranscriptionExpert(SpeechRecognitionExpert):
    """Backward-compatible alias for `SpeechRecognitionExpert`."""
