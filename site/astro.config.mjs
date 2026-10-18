// @ts-check
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import version from "./src/generated/version.json";

// Docs are versioned with the repo: the generator pins the release these
// pages were built from (latest `v*` git tag, or `edge` from a checkout).
const release = /** @type {{ version: string }} */ (version).version;

/** @type {import('@astrojs/starlight').StarlightUserConfig['sidebar']} */
const sidebar = [
    {
        label: "Get started",
        items: [
            "docs/get-started/quickstart",
            "docs/get-started/install",
            "docs/get-started/first-sandbox",
            "docs/get-started/mcp",
        ],
    },
    {
        label: "Guides",
        items: [
            "docs/guides/remote-servers",
            "docs/guides/macos",
            "docs/guides/windows",
            "docs/guides/policies",
            "docs/guides/vault-and-profiles",
            "docs/guides/domain-https-backups",
            "docs/guides/upgrading",
            "docs/guides/kubernetes",
            "docs/guides/building-on-zoo",
        ],
    },
    {
        label: "Concepts",
        items: ["docs/concepts/architecture", "docs/concepts/security-model", "docs/concepts/runtimes"],
    },
    {
        label: "Reference",
        items: [
            "docs/reference/conventions",
            "docs/reference/api",
            "docs/reference/tools",
            "docs/reference/sdk",
            "docs/reference/cli",
            "docs/reference/configuration",
            "docs/reference/errors",
            "docs/reference/performance",
        ],
    },
];

// The generated Reference pages carry the release they were generated from.
for (const group of sidebar) {
    if (group.label === "Reference") {
        // @ts-expect-error augmenting the group with a generated badge
        group.badge = { text: release, variant: "note" };
    }
}

export default defineConfig({
    integrations: [
        starlight({
            title: "Zoo",
            description: "Self-hosted sandboxes for AI agents.",
            logo: { src: "./src/assets/zoo-mark.png" },
            favicon: "/favicon.png",
            social: [{ icon: "github", label: "GitHub", href: "https://github.com/chann44/zoo" }],
            customCss: ["@fontsource-variable/archivo", "@fontsource/fragment-mono", "./src/styles/docs.css"],
            editLink: {
                baseUrl: "https://github.com/chann44/zoo/edit/main/site/",
            },
            lastUpdated: true,
            sidebar,
        }),
    ],
});
