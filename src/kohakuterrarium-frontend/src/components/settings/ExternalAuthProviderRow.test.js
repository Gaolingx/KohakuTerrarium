import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ElementPlus from "element-plus"
vi.mock("@/utils/api", () => ({
  settingsAPI: {
    getExternalAuthStatus: vi.fn(),
    getAntigravityStatus: vi.fn(),
    getGrokStatus: vi.fn(),
  },
}))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))
import { settingsAPI } from "@/utils/api"
import ExternalAuthProviderRow from "./ExternalAuthProviderRow.vue"

const backend = (overrides = {}) => ({
  name: "google-antigravity",
  backend_type: "google-antigravity",
  display_name: "Google Antigravity",
  description: "Reuse the local agy login.",
  source: "package",
  package: "kt-antigravity-login",
  owned_builtin: true,
  credentials: "external",
  available: true,
  auth: { local_only: true, login_hint: "Run agy to sign in, then check again.", available: true },
  ...overrides,
})

function row(props = {}) {
  return mount(ExternalAuthProviderRow, {
    props: { backend: backend(), ...props },
    global: { plugins: [ElementPlus] },
  })
}

describe("external auth provider row", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    settingsAPI.getExternalAuthStatus.mockResolvedValue({
      backend: "google-antigravity",
      state: "ready",
      source: "agy",
      refresh_available: true,
      hint: "",
    })
  })

  it("checks the generic endpoint once on mount and shows the package display name", async () => {
    const wrapper = row()
    await flushPromises()
    expect(settingsAPI.getExternalAuthStatus).toHaveBeenCalledTimes(1)
    expect(settingsAPI.getExternalAuthStatus).toHaveBeenCalledWith("google-antigravity", "_host")
    expect(settingsAPI.getAntigravityStatus).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain("Google Antigravity")
    expect(wrapper.text()).toContain("Reuse the local agy login.")
    expect(wrapper.text()).toContain("settings.oauth.ready")
    expect(wrapper.findAll("button")).toHaveLength(1)
    expect(wrapper.get("button").text()).toBe("settings.oauth.check")
  })

  it("falls back to the owning package when the backend has no description", async () => {
    const wrapper = row({ backend: backend({ description: "" }) })
    await flushPromises()
    expect(wrapper.text()).toContain("settings.providers.fromPackage")
  })

  it("shows awaiting refresh rather than an error when the provider can refresh", async () => {
    settingsAPI.getExternalAuthStatus.mockResolvedValue({
      state: "expired",
      refresh_available: true,
    })
    const wrapper = row()
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.pending")
    expect(wrapper.text()).toContain("settings.oauth.autoRefresh")
    expect(wrapper.text()).not.toContain("settings.oauth.expired")
  })

  it("shows an expired state when the provider cannot refresh", async () => {
    settingsAPI.getExternalAuthStatus.mockResolvedValue({
      state: "expired",
      refresh_available: false,
    })
    const wrapper = row()
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.expired")
    expect(wrapper.text()).toContain("Run agy to sign in, then check again.")
  })

  it("maps login_required to not-signed-in and unsupported_platform to unsupported", async () => {
    settingsAPI.getExternalAuthStatus.mockResolvedValue({
      state: "login_required",
      refresh_available: false,
    })
    const missing = row()
    await flushPromises()
    expect(missing.text()).toContain("settings.oauth.missing")

    settingsAPI.getExternalAuthStatus.mockResolvedValue({ state: "unsupported_platform" })
    const unsupported = row()
    await flushPromises()
    expect(unsupported.text()).toContain("settings.oauth.unsupported")
  })

  it("treats an unknown or unusable state as needing attention", async () => {
    settingsAPI.getExternalAuthStatus.mockResolvedValue({ state: "unavailable" })
    const wrapper = row()
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.attention")
  })

  it("prefers the runtime hint over the manifest login hint", async () => {
    settingsAPI.getExternalAuthStatus.mockResolvedValue({
      state: "login_required",
      hint: "Run agy login now.",
    })
    const wrapper = row()
    await flushPromises()
    expect(wrapper.text()).toContain("Run agy login now.")
    expect(wrapper.text()).not.toContain("Run agy to sign in, then check again.")
  })

  it("falls back to a generic sign-in prompt when the provider gives no hint", async () => {
    settingsAPI.getExternalAuthStatus.mockResolvedValue({ state: "login_required" })
    const wrapper = row({ backend: backend({ auth: { local_only: true, login_hint: "" } }) })
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.signInRequired")
  })

  it("does not call the provider for remote nodes when the provider is local only", async () => {
    const wrapper = row({ node: "worker" })
    await flushPromises()
    expect(settingsAPI.getExternalAuthStatus).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain("settings.oauth.unsupported")
    expect(wrapper.text()).toContain("settings.oauth.localOnly")
    expect(wrapper.get("button").attributes("disabled")).toBeDefined()
  })

  it("allows remote checks when the provider is not local only", async () => {
    const wrapper = row({ node: "worker", backend: backend({ auth: { local_only: false } }) })
    await flushPromises()
    expect(settingsAPI.getExternalAuthStatus).toHaveBeenCalledWith("google-antigravity", "worker")
  })

  it("distinguishes permission failures from missing login", async () => {
    settingsAPI.getExternalAuthStatus.mockRejectedValue({ response: { status: 403 } })
    const wrapper = row()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain("settings.oauth.adminRequired")
    expect(wrapper.text()).not.toContain("settings.oauth.missing")
  })

  it("reports a failed check without an alert when the network fails", async () => {
    settingsAPI.getExternalAuthStatus.mockRejectedValue(new Error("network"))
    const wrapper = row()
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.failed")
    expect(wrapper.text()).toContain("settings.oauth.checkFailed")
  })

  it("ignores a stale response after the node changes", async () => {
    let resolveOld
    settingsAPI.getExternalAuthStatus.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve
        }),
    )
    const wrapper = row({ backend: backend({ auth: { local_only: false } }) })
    settingsAPI.getExternalAuthStatus.mockResolvedValue({ state: "login_required" })
    await wrapper.setProps({ node: "worker" })
    await flushPromises()
    resolveOld({ state: "ready" })
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.missing")
    expect(wrapper.text()).not.toContain("settings.oauth.ready")
  })
})
