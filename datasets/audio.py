import librosa
import librosa.filters
import numpy as np
import tensorflow as tf
from scipy import signal
from scipy.io import wavfile


def mulaw(x, mu=256):
	mu = 255
	return _sign(x) * _log1p(mu * _abs(x)) / _log1p(mu)
def mulaw_quantize(x, mu=256):
	mu = 255
	y = mulaw(x, mu)
	# scale [-1, 1] to [0, mu]
	return _asint((y + 1) / 2 * mu)

def _sign(x):
	#wrapper to support tensorflow tensors/numpy arrays
	isnumpy = isinstance(x, np.ndarray)
	isscalar = np.isscalar(x)
	return np.sign(x) if (isnumpy or isscalar) else tf.sign(x)


def _log1p(x):
	#wrapper to support tensorflow tensors/numpy arrays
	isnumpy = isinstance(x, np.ndarray)
	isscalar = np.isscalar(x)
	return np.log1p(x) if (isnumpy or isscalar) else tf.log1p(x)


def _abs(x):
	#wrapper to support tensorflow tensors/numpy arrays
	isnumpy = isinstance(x, np.ndarray)
	isscalar = np.isscalar(x)
	return np.abs(x) if (isnumpy or isscalar) else tf.abs(x)


def _asint(x):
	#wrapper to support tensorflow tensors/numpy arrays
	isnumpy = isinstance(x, np.ndarray)
	isscalar = np.isscalar(x)
	return x.astype(np.int) if isnumpy else int(x) if isscalar else tf.cast(x, tf.int32)


def _asfloat(x):
	#wrapper to support tensorflow tensors/numpy arrays
	isnumpy = isinstance(x, np.ndarray)
	isscalar = np.isscalar(x)
	return x.astype(np.float32) if isnumpy else float(x) if isscalar else tf.cast(x, tf.float32)

def _assert_valid_input_type(s):
	assert s == 'mulaw-quantize' or s == 'mulaw' or s == 'raw'

def is_mulaw_quantize(s):
	_assert_valid_input_type(s)
	return s == 'mulaw-quantize'

def is_mulaw(s):
	_assert_valid_input_type(s)
	return s == 'mulaw'

def is_raw(s):
	_assert_valid_input_type(s)
	return s == 'raw'


def load_wav(path, sr):
	return librosa.core.load(path, sr=sr)[0]

def save_wav(wav, path, sr):
	wav *= 32767 / max(0.01, np.max(np.abs(wav)))
	#proposed by @dsmiller
	wavfile.write(path, sr, wav.astype(np.int16))

def save_wavernn_wav(wav, path, sr):
    librosa.output.write_wav(path, wav, sr=sr)
def save_wavenet_wav(wav, path, sr, inv_preemphasize, k):
	# wav = inv_preemphasis(wav, k, inv_preemphasize)
	wav *= 32767 / max(0.01, np.max(np.abs(wav)))
	wavfile.write(path, sr, wav.astype(np.int16))

def preemphasis(wav, k, preemphasize=True):
	if preemphasize:
		return signal.lfilter([1, -k], [1], wav)
	return wav

def inv_preemphasis(wav, k, inv_preemphasize=True):
	if inv_preemphasize:
		return signal.lfilter([1], [1, -k], wav)
	return wav

#From https://github.com/r9y9/wavenet_vocoder/blob/master/audio.py
def start_and_end_indices(quantized, silence_threshold=2):
	for start in range(quantized.size):
		if abs(quantized[start] - 127) > silence_threshold:
			break
	for end in range(quantized.size - 1, 1, -1):
		if abs(quantized[end] - 127) > silence_threshold:
			break

	assert abs(quantized[start] - 127) > silence_threshold
	assert abs(quantized[end] - 127) > silence_threshold

	return start, end

def trim_silence(wav, hparams):
	'''Trim leading and trailing silence

	Useful for M-AILABS dataset if we choose to trim the extra 0.5 silence at beginning and end.
	'''
	#Thanks @begeekmyfriend and @lautjy for pointing out the params contradiction. These params are separate and tunable per dataset.
	return librosa.effects.trim(wav, top_db= hparams.trim_top_db, frame_length=hparams.trim_fft_size, hop_length=hparams.trim_hop_size)[0]

def get_hop_size(hparams):
	hop_size = hparams.hop_size
	if hop_size is None:
		assert hparams.frame_shift_ms is not None
		hop_size = int(hparams.frame_shift_ms / 1000 * hparams.sample_rate)
	return hop_size

def linearspectrogram(wav, hparams):
	# D = _stft(preemphasis(wav, hparams.preemphasis, hparams.preemphasize), hparams)
	D = _stft(wav, hparams)
	S = _amp_to_db(np.abs(D)**hparams.magnitude_power, hparams) - hparams.ref_level_db

	if hparams.signal_normalization:
		return _normalize(S, hparams)
	return S

def melspectrogram(wav, hparams):
	# D = _stft(preemphasis(wav, hparams.preemphasis, hparams.preemphasize), hparams)
	D = _stft(wav, hparams)
	S = _amp_to_db(_linear_to_mel(np.abs(D)**hparams.magnitude_power, hparams), hparams) - hparams.ref_level_db

	if hparams.signal_normalization:
		return _normalize(S, hparams)
	return S

def inv_linear_spectrogram(linear_spectrogram, hparams):
	'''Converts linear spectrogram to waveform using librosa'''
	if hparams.signal_normalization:
		D = _denormalize(linear_spectrogram, hparams)
	else:
		D = linear_spectrogram

	S = _db_to_amp(D + hparams.ref_level_db)**(1/hparams.magnitude_power) #Convert back to linear

	if hparams.use_lws:
		processor = _lws_processor(hparams)
		D = processor.run_lws(S.astype(np.float64).T ** hparams.power)
		y = processor.istft(D).astype(np.float32)
		return inv_preemphasis(y, hparams.preemphasis, hparams.preemphasize)
	else:
		return inv_preemphasis(_griffin_lim(S ** hparams.power, hparams), hparams.preemphasis, hparams.preemphasize)


def inv_mel_spectrogram(mel_spectrogram, hparams):
	'''Converts mel spectrogram to waveform using librosa'''
	if hparams.signal_normalization:
		D = _denormalize(mel_spectrogram, hparams)
	else:
		D = mel_spectrogram

	S = _mel_to_linear(_db_to_amp(D + hparams.ref_level_db)**(1/hparams.magnitude_power), hparams)  # Convert back to linear

	if hparams.use_lws:
		processor = _lws_processor(hparams)
		D = processor.run_lws(S.astype(np.float64).T ** hparams.power)
		y = processor.istft(D).astype(np.float32)
		return inv_preemphasis(y, hparams.preemphasis, hparams.preemphasize)
	else:
		return inv_preemphasis(_griffin_lim(S ** hparams.power, hparams), hparams.preemphasis, hparams.preemphasize)

def _lws_processor(hparams):
	import lws
	return lws.lws(hparams.n_fft, get_hop_size(hparams), fftsize=hparams.win_size, mode="speech")

def _griffin_lim(S, hparams):
	'''librosa implementation of Griffin-Lim
	Based on https://github.com/librosa/librosa/issues/434
	'''
	angles = np.exp(2j * np.pi * np.random.rand(*S.shape))
	S_complex = np.abs(S).astype(np.complex)
	y = _istft(S_complex * angles, hparams)
	for i in range(hparams.griffin_lim_iters):
		angles = np.exp(1j * np.angle(_stft(y, hparams)))
		y = _istft(S_complex * angles, hparams)
	return y

def _stft(y, hparams):
	if hparams.use_lws:
		return _lws_processor(hparams).stft(y).T
	else:
		return librosa.stft(y=y, n_fft=hparams.n_fft, hop_length=get_hop_size(hparams), win_length=hparams.win_size, pad_mode='constant')

def _istft(y, hparams):
	return librosa.istft(y, hop_length=get_hop_size(hparams), win_length=hparams.win_size)

##########################################################
#Those are only correct when using lws!!! (This was messing with Wavenet quality for a long time!)
def num_frames(length, fsize, fshift):
	"""Compute number of time frames of spectrogram
	"""
	pad = (fsize - fshift)
	if length % fshift == 0:
		M = (length + pad * 2 - fsize) // fshift + 1
	else:
		M = (length + pad * 2 - fsize) // fshift + 2
	return M


def pad_lr(x, fsize, fshift):
	"""Compute left and right padding
	"""
	M = num_frames(len(x), fsize, fshift)
	pad = (fsize - fshift)
	T = len(x) + 2 * pad
	r = (M - 1) * fshift + fsize - T
	return pad, pad + r
##########################################################
#Librosa correct padding
def librosa_pad_lr(x, fsize, fshift, pad_sides=1):
	'''compute right padding (final frame) or both sides padding (first and final frames)
	'''
	assert pad_sides in (1, 2)
	# return int(fsize // 2)
	pad = (x.shape[0] // fshift + 1) * fshift - x.shape[0]
	if pad_sides == 1:
		return 0, pad
	else:
		return pad // 2, pad // 2 + pad % 2

# Conversions
_mel_basis = None
_inv_mel_basis = None

def _linear_to_mel(spectogram, hparams):
	global _mel_basis
	if _mel_basis is None:
		_mel_basis = _build_mel_basis(hparams)
	return np.dot(_mel_basis, spectogram)

def _mel_to_linear(mel_spectrogram, hparams):
	global _inv_mel_basis
	if _inv_mel_basis is None:
		_inv_mel_basis = np.linalg.pinv(_build_mel_basis(hparams))
	return np.maximum(1e-10, np.dot(_inv_mel_basis, mel_spectrogram))

def _build_mel_basis(hparams):
	assert hparams.fmax <= hparams.sample_rate // 2
	return librosa.filters.mel(hparams.sample_rate, hparams.n_fft, n_mels=hparams.num_mels,
							   fmin=hparams.fmin, fmax=hparams.fmax)

def _amp_to_db(x, hparams):
	min_level = np.exp(hparams.min_level_db / 20 * np.log(10))
	return 20 * np.log10(np.maximum(min_level, x))

def _db_to_amp(x):
	return np.power(10.0, (x) * 0.05)

def _normalize(S, hparams):
	if hparams.allow_clipping_in_normalization:
		if hparams.symmetric_mels:
			return np.clip((2 * hparams.max_abs_value) * ((S - hparams.min_level_db) / (-hparams.min_level_db)) - hparams.max_abs_value,
			 -hparams.max_abs_value, hparams.max_abs_value)
		else:
			return np.clip(hparams.max_abs_value * ((S - hparams.min_level_db) / (-hparams.min_level_db)), 0, hparams.max_abs_value)

	assert S.max() <= 0 and S.min() - hparams.min_level_db >= 0
	if hparams.symmetric_mels:
		return (2 * hparams.max_abs_value) * ((S - hparams.min_level_db) / (-hparams.min_level_db)) - hparams.max_abs_value
	else:
		return hparams.max_abs_value * ((S - hparams.min_level_db) / (-hparams.min_level_db))

def _denormalize(D, hparams):
	if hparams.allow_clipping_in_normalization:
		if hparams.symmetric_mels:
			return (((np.clip(D, -hparams.max_abs_value,
				hparams.max_abs_value) + hparams.max_abs_value) * -hparams.min_level_db / (2 * hparams.max_abs_value))
				+ hparams.min_level_db)
		else:
			return ((np.clip(D, 0, hparams.max_abs_value) * -hparams.min_level_db / hparams.max_abs_value) + hparams.min_level_db)

	if hparams.symmetric_mels:
		return (((D + hparams.max_abs_value) * -hparams.min_level_db / (2 * hparams.max_abs_value)) + hparams.min_level_db)
	else:
		return ((D * -hparams.min_level_db / hparams.max_abs_value) + hparams.min_level_db)

def normalize_tf(S, hparams):
	#[0, 1]
	if hparams.normalize_for_wavenet:
		if hparams.allow_clipping_in_normalization:
			return tf.minimum(tf.maximum((S - hparams.min_level_db) / (-hparams.min_level_db),
			 -hparams.max_abs_value), hparams.max_abs_value)

		else:
			return (S - hparams.min_level_db) / (-hparams.min_level_db)
	
	#[-max, max] or [0, max]
	if hparams.allow_clipping_in_normalization:
		if hparams.symmetric_mels:
			return tf.minimum(tf.maximum((2 * hparams.max_abs_value) * ((S - hparams.min_level_db) / (-hparams.min_level_db)) - hparams.max_abs_value,
			 -hparams.max_abs_value), hparams.max_abs_value)
		else:
			return tf.minimum(tf.maximum(hparams.max_abs_value * ((S - hparams.min_level_db) / (-hparams.min_level_db)), 0), hparams.max_abs_value)

	assert S.max() <= 0 and S.min() - hparams.min_level_db >= 0
	if hparams.symmetric_mels:
		return (2 * hparams.max_abs_value) * ((S - hparams.min_level_db) / (-hparams.min_level_db)) - hparams.max_abs_value
	else:
		return hparams.max_abs_value * ((S - hparams.min_level_db) / (-hparams.min_level_db))
