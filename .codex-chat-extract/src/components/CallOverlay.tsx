import { useEffect, useRef, useState } from 'react';
import { Phone, PhoneOff, Video, VideoOff, Mic, MicOff, Volume2, User as UserIcon } from 'lucide-react';
import { motion } from 'motion/react';
import { User } from '../types';

interface CallOverlayProps {
  currentCall: {
    status: 'connecting' | 'ringing' | 'connected' | 'ended' | 'declined';
    isVideo: boolean;
    callerId: string;
    receiverId: string;
    role: 'caller' | 'receiver';
    callId: string;
    peer: User;
  } | null;
  onAccept: () => void;
  onDecline: () => void;
  onHangup: (duration: number) => void;
}

export default function CallOverlay({ currentCall, onAccept, onDecline, onHangup }: CallOverlayProps) {
  if (!currentCall) return null;

  const [isMuted, setIsMuted] = useState(false);
  const [isCameraOff, setIsCameraOff] = useState(!currentCall.isVideo);
  const [seconds, setSeconds] = useState(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const synthIntervalRef = useRef<any>(null);

  // Active call duration timer
  useEffect(() => {
    let interval: any = null;
    if (currentCall.status === 'connected') {
      interval = setInterval(() => {
        setSeconds((prev) => prev + 1);
      }, 1000);
    } else {
      setSeconds(0);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [currentCall.status]);

  // Audio Ringtones Synthesizer using Web Audio API (No files required!)
  useEffect(() => {
    // Basic Beep Ringtone synth
    const startRingTone = () => {
      try {
        const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        audioCtxRef.current = ctx;

        let time = ctx.currentTime;
        if (currentCall.status === 'ringing' && currentCall.role === 'receiver') {
          // Inbound Ringtone: repeating pleasant dual tone
          synthIntervalRef.current = setInterval(() => {
            if (ctx.state === 'suspended') ctx.resume();
            const osc1 = ctx.createOscillator();
            const osc2 = ctx.createOscillator();
            const gainNode = ctx.createGain();

            osc1.type = 'sine';
            osc2.type = 'sine';
            osc1.frequency.setValueAtTime(440, ctx.currentTime);
            osc2.frequency.setValueAtTime(480, ctx.currentTime);

            gainNode.gain.setValueAtTime(0, ctx.currentTime);
            gainNode.gain.linearRampToValueAtTime(0.15, ctx.currentTime + 0.1);
            gainNode.gain.linearRampToValueAtTime(0, ctx.currentTime + 1.2);

            osc1.connect(gainNode);
            osc2.connect(gainNode);
            gainNode.connect(ctx.destination);

            osc1.start();
            osc2.start();
            osc1.stop(ctx.currentTime + 1.5);
            osc2.stop(ctx.currentTime + 1.5);
          }, 2000);
        } else if (currentCall.status === 'connecting' && currentCall.role === 'caller') {
          // Outbound Ringback: typical soft double rings
          synthIntervalRef.current = setInterval(() => {
            if (ctx.state === 'suspended') ctx.resume();
            const osc = ctx.createOscillator();
            const gainNode = ctx.createGain();

            osc.type = 'sine';
            osc.frequency.setValueAtTime(425, ctx.currentTime);

            gainNode.gain.setValueAtTime(0, ctx.currentTime);
            gainNode.gain.linearRampToValueAtTime(0.1, ctx.currentTime + 0.1);
            gainNode.gain.linearRampToValueAtTime(0, ctx.currentTime + 1.4);

            osc.connect(gainNode);
            gainNode.connect(ctx.destination);

            osc.start();
            osc.stop(ctx.currentTime + 1.5);
          }, 2500);
        }
      } catch (err) {
        console.warn('Web Audio Ringtone synth failed:', err);
      }
    };

    startRingTone();

    return () => {
      if (synthIntervalRef.current) clearInterval(synthIntervalRef.current);
      if (audioCtxRef.current) {
        audioCtxRef.current.close().catch(() => {});
        audioCtxRef.current = null;
      }
    };
  }, [currentCall.status, currentCall.role]);

  // Handle local camera and microphone stream
  useEffect(() => {
    const enableCameraStream = async () => {
      if (currentCall.status === 'connected' && !isCameraOff) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({
            video: { width: 320, height: 240, facingMode: 'user' },
            audio: false, // audio output managed through system calling signaling logic
          });
          streamRef.current = stream;
          if (videoRef.current) {
            videoRef.current.srcObject = stream;
          }
        } catch (err) {
          console.warn('Could not launch camera device stream:', err);
          setIsCameraOff(true);
        }
      }
    };

    if (currentCall.status === 'connected') {
      enableCameraStream();
    } else {
      stopCameraStream();
    }

    return () => {
      stopCameraStream();
    };
  }, [currentCall.status, isCameraOff]);

  const stopCameraStream = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  };

  const toggleMute = () => {
    setIsMuted(!isMuted);
  };

  const toggleCamera = () => {
    setIsCameraOff(!isCameraOff);
  };

  const formatTimer = (totalSeconds: number) => {
    const mins = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  return (
    <div id="call-overlay" className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/95 p-4 text-white backdrop-blur-md">
      <div className="relative flex h-full max-h-[640px] w-full max-w-[420px] flex-col items-center justify-between rounded-3xl bg-gray-800/80 p-8 shadow-2xl backdrop-blur-xl border border-gray-700/50">
        
        {/* Top Status */}
        <div className="text-center mt-4">
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/10 text-xs font-medium uppercase tracking-wider text-sky-400">
            {currentCall.isVideo ? 'Video Call' : 'Voice Call'}
          </span>
          <h2 className="mt-4 text-2xl font-bold tracking-tight text-white">{currentCall.peer.name}</h2>
          <p className="mt-1 text-sm text-gray-400">
            {currentCall.status === 'connecting' && 'Connecting...'}
            {currentCall.status === 'ringing' && 'Ringing...'}
            {currentCall.status === 'connected' && `In Call • ${formatTimer(seconds)}`}
          </p>
        </div>

        {/* Center Visuals */}
        <div className="relative flex flex-1 items-center justify-center py-8 w-full">
          {currentCall.status === 'connected' && !isCameraOff ? (
            <div className="relative h-64 w-64 overflow-hidden rounded-2xl bg-black border-2 border-sky-400/50 shadow-lg">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="h-full w-full object-cover scale-x-[-1]"
              />
              <span className="absolute bottom-2 left-2 px-2 py-0.5 rounded bg-black/60 text-[10px] text-gray-300">
                Local Feed
              </span>
            </div>
          ) : (
            <div className="relative">
              {/* Pulsing rings for calling screen */}
              {['connecting', 'ringing'].includes(currentCall.status) && (
                <>
                  <div className="absolute inset-0 animate-ping rounded-full bg-sky-500/20 opacity-75 scale-150" />
                  <div className="absolute inset-0 animate-pulse rounded-full bg-sky-500/10 scale-125" />
                </>
              )}
              {currentCall.peer.avatar ? (
                <img
                  src={currentCall.peer.avatar}
                  alt={currentCall.peer.name}
                  className="relative h-32 w-32 rounded-full object-cover border-4 border-gray-700 shadow-xl"
                  referrerPolicy="no-referrer"
                />
              ) : (
                <div className="relative flex h-32 w-32 items-center justify-center rounded-full bg-sky-600 border-4 border-gray-700 text-4xl font-bold uppercase shadow-xl">
                  {currentCall.peer.name[0]}
                </div>
              )}
              
              {/* Active Audio Waveform mock animations */}
              {currentCall.status === 'connected' && isCameraOff && (
                <div className="absolute -bottom-6 left-1/2 -translate-x-1/2 flex items-end gap-1 h-8 px-3 py-1 rounded-full bg-white/5">
                  <div className="w-1 bg-sky-400 rounded-sm animate-[bounce_0.8s_infinite_100ms] h-3" />
                  <div className="w-1 bg-sky-400 rounded-sm animate-[bounce_0.6s_infinite_300ms] h-6" />
                  <div className="w-1 bg-sky-400 rounded-sm animate-[bounce_0.7s_infinite_200ms] h-4" />
                  <div className="w-1 bg-sky-400 rounded-sm animate-[bounce_0.9s_infinite_400ms] h-5" />
                  <div className="w-1 bg-sky-400 rounded-sm animate-[bounce_0.5s_infinite_150ms] h-2" />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Bottom Interactive Controls */}
        <div className="w-full flex flex-col gap-6 items-center">
          
          {/* Answer Controls for Receiver */}
          {currentCall.status === 'ringing' && currentCall.role === 'receiver' ? (
            <div className="flex w-full justify-around gap-4 px-4">
              <button
                id="btn-decline-call"
                onClick={onDecline}
                className="flex h-14 w-14 items-center justify-center rounded-full bg-red-600 hover:bg-red-700 transition shadow-lg shadow-red-600/30 active:scale-95"
              >
                <PhoneOff className="h-6 w-6 text-white" />
              </button>
              <button
                id="btn-accept-call"
                onClick={onAccept}
                className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-600 hover:bg-emerald-700 transition shadow-lg shadow-emerald-600/30 animate-bounce active:scale-95"
              >
                <Phone className="h-6 w-6 text-white" />
              </button>
            </div>
          ) : (
            /* Active controls */
            <div className="flex gap-4 items-center">
              {/* Mute Mic */}
              <button
                id="btn-toggle-mute"
                onClick={toggleMute}
                className={`flex h-12 w-12 items-center justify-center rounded-full transition active:scale-95 ${
                  isMuted ? 'bg-red-500 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                }`}
                title={isMuted ? 'Unmute microphone' : 'Mute microphone'}
              >
                {isMuted ? <MicOff className="h-5 w-5" /> : <Mic className="h-5 w-5" />}
              </button>

              {/* End Call / Hang Up */}
              <button
                id="btn-hangup-call"
                onClick={() => onHangup(seconds)}
                className="flex h-14 w-14 items-center justify-center rounded-full bg-red-600 hover:bg-red-700 transition shadow-lg shadow-red-600/30 active:scale-95"
                title="Hang up"
              >
                <PhoneOff className="h-6 w-6 text-white" />
              </button>

              {/* Toggle Video Feed */}
              {currentCall.isVideo && (
                <button
                  id="btn-toggle-video"
                  onClick={toggleCamera}
                  className={`flex h-12 w-12 items-center justify-center rounded-full transition active:scale-95 ${
                    isCameraOff ? 'bg-red-500 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                  }`}
                  title={isCameraOff ? 'Turn on Video' : 'Turn off Video'}
                >
                  {isCameraOff ? <VideoOff className="h-5 w-5" /> : <Video className="h-5 w-5" />}
                </button>
              )}
            </div>
          )}

          <div className="text-[11px] text-gray-500 font-mono tracking-wider flex items-center gap-1">
            <Volume2 className="h-3 w-3" /> Standard Audio Processing
          </div>
        </div>
      </div>
    </div>
  );
}
