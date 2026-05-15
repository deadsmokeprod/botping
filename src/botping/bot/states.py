from aiogram.fsm.state import State, StatesGroup


class AddBotStates(StatesGroup):
    waiting_name = State()
    waiting_token = State()


class SettingStates(StatesGroup):
    waiting_value = State()


class QuietHoursStates(StatesGroup):
    waiting_json = State()


class ReportStates(StatesGroup):
    waiting_period = State()


class AddRouterStates(StatesGroup):
    waiting_name = State()


class AddTargetStates(StatesGroup):
    waiting_name = State()
    waiting_address = State()
