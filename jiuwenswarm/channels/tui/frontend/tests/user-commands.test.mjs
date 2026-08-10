import assert from "node:assert/strict";

import { CommandService } from "../dist/core/commands/CommandService.js";
import { CommandKind } from "../dist/core/commands/types.js";
import { createHelpCommand } from "../dist/core/commands/builtins/help.js";
import { buildUserCommands } from "../dist/core/commands/user-commands.js";

/** One definition as `commands.list` returns it. */
function definition(overrides = {}) {
  return {
    name: "review",
    description: "Review a file",
    source: "project",
    file_path: "/ws/.jiuwenswarm/commands/review.md",
    argument_hint: "<path>",
    allowed_tools: null,
    accepts_args: true,
    shadowed_by: null,
    reserved: false,
    ...overrides,
  };
}

function makeMockContext({ responses = {}, failOn = new Set() } = {}) {
  const requests = [];
  const sent = [];
  const items = [];
  const ctx = {
    sessionId: "s-1",
    addItem: (item) => items.push(item),
    sendMessage: (content) => {
      sent.push(content);
      return "msg-1";
    },
    request: async (method, params) => {
      requests.push({ method, params });
      if (failOn.has(method)) throw new Error(`${method} exploded`);
      return responses[method] ?? {};
    },
  };
  return { ctx, requests, sent, items };
}

function builtin(name) {
  return {
    name,
    description: `built-in ${name}`,
    kind: CommandKind.BUILT_IN,
    action: () => {},
  };
}

// ------------------------------------------------------------ registration

{
  // A user command becomes runnable, and the built-ins survive the merge.
  const service = new CommandService();
  service.register([builtin("model"), builtin("help")]);
  const { ctx, requests } = makeMockContext({
    responses: { "commands.list": { commands: [definition()] } },
  });

  await service.refreshUserCommands(ctx);

  const resolved = service.resolve("review");
  assert.ok(resolved, "user command should be registered");
  assert.equal(resolved.kind, CommandKind.USER);
  assert.equal(resolved.usage, "/review <path>");
  assert.match(resolved.description, /\(project\)/, "scope should be visible");
  assert.ok(service.resolve("model"), "built-ins must survive the rebuild");

  // The server cannot know the built-ins; it can only refuse a name we declare.
  const listCall = requests.find((r) => r.method === "commands.list");
  assert.ok(listCall.params.builtin_names.includes("model"));
  assert.ok(listCall.params.builtin_names.includes("help"));
}

{
  // A user file cannot take over a built-in, even if the server let it through.
  const service = new CommandService();
  service.register([builtin("model")]);
  const { ctx } = makeMockContext({
    responses: {
      "commands.list": { commands: [definition({ name: "model" })] },
    },
  });

  await service.refreshUserCommands(ctx);

  assert.equal(service.resolve("model").kind, CommandKind.BUILT_IN);
  const inactive = service.getInactiveUserCommands();
  assert.equal(inactive.length, 1);
  assert.match(inactive[0].reason, /reserved/);
}

{
  // Wire names are normalized to lowercase for collision guards.
  const { commands, inactive } = buildUserCommands(
    [definition({ name: "Review" })],
    new Set(["model"]),
    () => {},
  );
  assert.equal(commands.length, 1);
  assert.equal(commands[0].name, "review");
  assert.equal(inactive.length, 0);

  const reserved = buildUserCommands(
    [definition({ name: "Model" })],
    new Set(["model"]),
    () => {},
  );
  assert.equal(reserved.commands.length, 0);
  assert.equal(reserved.inactive.length, 1);
  assert.match(reserved.inactive[0].reason, /reserved/);

  const dup = buildUserCommands(
    [
      definition({ name: "Review", source: "project" }),
      definition({ name: "review", source: "user", shadowed_by: null }),
    ],
    new Set(),
    () => {},
  );
  assert.equal(dup.commands.length, 1);
  assert.equal(dup.inactive.length, 1);
  assert.match(dup.inactive[0].reason, /duplicate/);
}

{
  // A shadowed definition is reported, not silently absent.
  const service = new CommandService();
  service.register([]);
  const { ctx } = makeMockContext({
    responses: {
      "commands.list": {
        commands: [
          definition({ source: "user", shadowed_by: "project" }),
          definition({ source: "project" }),
        ],
      },
    },
  });

  await service.refreshUserCommands(ctx);

  assert.ok(service.resolve("review"));
  const inactive = service.getInactiveUserCommands();
  assert.equal(inactive.length, 1);
  assert.match(inactive[0].reason, /shadowed by the project/);
}

{
  // A dropped connection must not make the user's commands disappear.
  const service = new CommandService();
  service.register([]);
  const ok = makeMockContext({
    responses: { "commands.list": { commands: [definition()] } },
  });
  await service.refreshUserCommands(ok.ctx);
  assert.ok(service.resolve("review"));

  const broken = makeMockContext({ failOn: new Set(["commands.list"]) });
  await service.refreshUserCommands(broken.ctx);
  assert.ok(service.resolve("review"), "previous set should be kept");
}

{
  // An unresolved workspace must not wipe a previously loaded registry.
  const service = new CommandService();
  service.register([]);
  const loaded = makeMockContext({
    responses: {
      "commands.list": { commands: [definition()], workspace_resolved: true },
    },
  });
  await service.refreshUserCommands(loaded.ctx);
  assert.ok(service.resolve("review"));

  const unresolved = makeMockContext({
    responses: {
      "commands.list": { commands: [], workspace_resolved: false },
    },
  });
  await service.refreshUserCommands(unresolved.ctx);
  assert.ok(service.resolve("review"), "cache kept when workspace unresolved");
}

{
  // Refreshing replaces user commands without disturbing the built-ins.
  const service = new CommandService();
  service.register([builtin("model")]);
  const first = makeMockContext({
    responses: { "commands.list": { commands: [definition({ name: "old" })] } },
  });
  await service.refreshUserCommands(first.ctx);
  assert.ok(service.resolve("old"));

  const second = makeMockContext({
    responses: { "commands.list": { commands: [definition({ name: "new" })] } },
  });
  await service.refreshUserCommands(second.ctx);
  assert.equal(service.resolve("old"), undefined, "removed file should go away");
  assert.ok(service.resolve("new"));
  assert.ok(service.resolve("model"));
}

// ------------------------------------------------------------ execution

{
  // The first /name after creating a command file must work without a prior
  // refreshUserCommands call — execute() re-reads the registry first.
  const service = new CommandService();
  service.register([]);
  const { ctx, requests, sent } = makeMockContext({
    responses: {
      "commands.list": { commands: [definition()] },
      "commands.expand": { text: "Review CODE", embedded: ["src/a.py"], errors: [] },
    },
  });

  await service.execute("/review src/a.py", ctx);

  const listCalls = requests.filter((r) => r.method === "commands.list");
  assert.equal(listCalls.length, 1, "execute must refresh before lookup");
  assert.ok(requests.some((r) => r.method === "commands.expand"));
  assert.deepEqual(sent, ["Review CODE"]);
}

{
  // Running a command expands it server-side and sends the result.
  const service = new CommandService();
  service.register([]);
  const { ctx, requests, sent } = makeMockContext({
    responses: {
      "commands.list": { commands: [definition()] },
      "commands.expand": { text: "Review CODE", embedded: ["src/a.py"], errors: [] },
    },
  });
  await service.refreshUserCommands(ctx);

  await service.execute("/review src/a.py", ctx);

  const expandCall = requests.find((r) => r.method === "commands.expand");
  assert.ok(expandCall, "execution must go through commands.expand");
  assert.equal(expandCall.params.name, "review");
  assert.equal(expandCall.params.args, "src/a.py");
  assert.deepEqual(sent, ["Review CODE"]);
}

{
  // An unreadable @file is reported, and the usable text still goes out.
  const service = new CommandService();
  service.register([]);
  const { ctx, sent, items } = makeMockContext({
    responses: {
      "commands.list": { commands: [definition()] },
      "commands.expand": {
        text: "Review [could not read @gone.py: not found]",
        embedded: [],
        errors: ["gone.py: not found"],
      },
    },
  });
  await service.refreshUserCommands(ctx);

  await service.execute("/review gone.py", ctx);

  assert.equal(sent.length, 1, "a partial expansion is still worth sending");
  assert.ok(items.some((i) => JSON.stringify(i).includes("gone.py")));
}

{
  // A failed expansion sends nothing.
  const service = new CommandService();
  service.register([]);
  const failing = makeMockContext({
    responses: { "commands.list": { commands: [definition()] } },
    failOn: new Set(["commands.expand"]),
  });
  await service.execute("/review x", failing.ctx);

  assert.equal(failing.sent.length, 0, "nothing should reach the model");
  assert.ok(failing.items.some((i) => JSON.stringify(i).includes("failed to expand")));
}

{
  // An expansion that resolves to nothing sends nothing.
  const service = new CommandService();
  service.register([]);
  const { ctx, sent, items } = makeMockContext({
    responses: {
      "commands.list": { commands: [definition()] },
      "commands.expand": { text: "   ", errors: [] },
    },
  });
  await service.refreshUserCommands(ctx);

  await service.execute("/review", ctx);

  assert.equal(sent.length, 0);
  assert.ok(items.some((i) => JSON.stringify(i).includes("expanded to nothing")));
}

{
  // Top-level built-ins should not trigger a commands.list round trip.
  const service = new CommandService();
  const help = createHelpCommand(
    () => service.getAll(true),
    () => service.getInactiveUserCommands(),
  );
  service.register([help]);
  const { ctx, requests } = makeMockContext({});
  await service.execute("/help", ctx);
  assert.equal(
    requests.filter((r) => r.method === "commands.list").length,
    0,
    "built-ins must not refresh user commands",
  );
}

// ------------------------------------------------------------ /help

{
  // /help must describe the live registry, or a command is runnable and
  // undiscoverable at the same time.
  const service = new CommandService();
  const help = createHelpCommand(
    () => service.getAll(true),
    () => service.getInactiveUserCommands(),
  );
  service.register([help, builtin("model")]);
  const { ctx, items } = makeMockContext({
    responses: {
      "commands.list": {
        commands: [definition(), definition({ name: "model" })],
      },
    },
  });
  await service.refreshUserCommands(ctx);

  await service.execute("/help", ctx);

  const rendered = items.find((i) => JSON.stringify(i).includes("Slash Commands"));
  assert.ok(rendered, "help should render");
  const flat = JSON.stringify(rendered);
  assert.ok(flat.includes("User-defined"), "user commands need their own group");
  assert.ok(flat.includes("/review"), "the user command should be listed");
  assert.ok(flat.includes("not loaded"), "a file that does nothing must say why");
  assert.ok(flat.includes("reserved"), "the reason should be shown");
}

console.log("user-commands.test.mjs: ok");
