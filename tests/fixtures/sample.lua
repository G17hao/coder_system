-- sample.lua for testing file tools
local M = {}

function M.init(self)
    self.name = "test"
    self.value = 42
end

function M.getValue(self)
    return self.value
end

return M
