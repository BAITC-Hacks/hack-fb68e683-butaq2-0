import { VideoBackground } from "@/components/ui/video-background";

export function LoginBackground() {
  return <VideoBackground src="/media/admin-login-asme.mp4" poster="/media/admin-login-asme.jpg" className="admin-signin-backdrop" controlClassName="admin-background-toggle" />;
}
