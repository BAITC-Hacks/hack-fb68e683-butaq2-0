"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";

type VideoBackgroundProps = { src: string; poster: string; className: string; controlClassName: string };

export function VideoBackground({ src, poster, className, controlClassName }: VideoBackgroundProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const syncMotion = () => setPlaying(!motion.matches);
    syncMotion();
    motion.addEventListener("change", syncMotion);
    return () => motion.removeEventListener("change", syncMotion);
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || failed) return;
    const syncPlayback = () => {
      if (playing && !document.hidden) void video.play().catch(() => setPlaying(false));
      else video.pause();
    };
    syncPlayback();
    document.addEventListener("visibilitychange", syncPlayback);
    return () => document.removeEventListener("visibilitychange", syncPlayback);
  }, [playing, failed]);

  return (
    <>
      <div className={className} style={{ backgroundImage: `url("${poster}")` }} aria-hidden="true">
        {!failed && <video ref={videoRef} className="absolute inset-0 h-full w-full object-cover" src={src} poster={poster} muted loop playsInline preload="none" tabIndex={-1} onError={() => setFailed(true)} />}
      </div>
      {!failed && <button type="button" className={controlClassName} aria-label={playing ? "Pause background animation" : "Play background animation"} onClick={() => setPlaying((value) => !value)}>{playing ? <Pause size={16} /> : <Play size={16} />}</button>}
    </>
  );
}
