"use client";

import { useEffect, useRef, useState } from "react";
import { Camera, RefreshCw } from "lucide-react";

export function FaceCapture({ onImage }: { onImage: (image: Blob) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [active, setActive] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState("");

  useEffect(() => () => { stream.current?.getTracks().forEach((track) => track.stop()); }, []);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  async function start() {
    setError("");
    try {
      stream.current?.getTracks().forEach((track) => track.stop());
      const camera = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 960 }, height: { ideal: 720 } }, audio: false });
      stream.current = camera;
      setPreview("");
      setActive(true);
      if (video.current) { video.current.srcObject = camera; await video.current.play(); }
    } catch { setError("Camera unavailable. Use the photo upload below instead."); setActive(false); }
  }

  function capture() {
    const element = video.current;
    if (!element?.videoWidth) { setError("Camera is still starting. Try again."); return; }
    const canvas = document.createElement("canvas");
    canvas.width = element.videoWidth;
    canvas.height = element.videoHeight;
    canvas.getContext("2d")?.drawImage(element, 0, 0);
    canvas.toBlob((blob) => {
      if (!blob) return;
      onImage(blob);
      setPreview(URL.createObjectURL(blob));
      stream.current?.getTracks().forEach((track) => track.stop());
      setActive(false);
    }, "image/jpeg", .88);
  }

  return <div className="admin-capture">
    {preview ? <img src={preview} alt="Captured face for verification" /> : <video ref={video} playsInline muted autoPlay aria-label="Camera preview" style={{ display: active ? "block" : "none" }} />}
    {!active && !preview && <div className="admin-capture-placeholder"><Camera size={28} /><span>Position your face in good light</span></div>}
    <div className="admin-capture-actions"><button type="button" onClick={active ? capture : start}>{active ? <><Camera size={15} /> Capture photo</> : <><RefreshCw size={15} /> {preview ? "Retake" : "Open camera"}</>}</button><label>Or upload a photo<input type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => { const file = event.target.files?.[0]; if (file) { onImage(file); setPreview(URL.createObjectURL(file)); stream.current?.getTracks().forEach((track) => track.stop()); setActive(false); } }} /></label></div>
    {error && <p className="admin-error" role="alert">{error}</p>}
  </div>;
}
