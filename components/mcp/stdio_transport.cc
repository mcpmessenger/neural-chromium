#include "components/mcp/stdio_transport.h"

#include "base/logging.h"
#include "base/task/thread_pool.h"

namespace mcp {

StdioTransport::StdioTransport(const std::string& command, const std::vector<std::string>& args)
    : command_(command), args_(args), reader_thread_("McpReaderThread") {}

StdioTransport::~StdioTransport() {
  Close();
}

void StdioTransport::Start(ReadCallback read_callback) {
  read_callback_ = std::move(read_callback);

  base::LaunchOptions options;
  options.start_hidden = true;
  // TODO: Setup pipe redirection properly using base::LaunchOptions
  // This is a placeholder for the actual pipe creation logic which is platform specific.
  // For Windows, we need to CreatePipe and pass handles.
  
  process_ = base::LaunchProcess(base::CommandLine(base::FilePath::FromASCII(command_)), options);
  
  if (!process_.IsValid()) {
    LOG(ERROR) << "Failed to launch MCP process: " << command_;
    return;
  }

  running_ = true;
  reader_thread_.Start();
  reader_thread_.task_runner()->PostTask(
      FROM_HERE, base::BindOnce(&StdioTransport::ReadLoop, weak_factory_.GetWeakPtr()));
}

void StdioTransport::Send(const std::string& message) {
    // TODO: Write to stdin_file_
}

bool StdioTransport::IsConnected() const {
  return process_.IsValid() && running_;
}

void StdioTransport::Close() {
  running_ = false;
  if (process_.IsValid()) {
    process_.Terminate(0, false);
  }
}

void StdioTransport::ReadLoop() {
  // TODO: Read from stdout_file_ line by line (JSON-RPC is usually newline delimited)
  // while (running_) { ... read_callback_.Run(line); ... }
}

}  // namespace mcp
