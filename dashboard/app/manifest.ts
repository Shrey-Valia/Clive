import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Clive Terminal", short_name: "Clive", start_url: "/", display: "standalone",
    background_color: "#000000", theme_color: "#000000",
    description: "Every bot, its positions and its performance.",
  };
}
