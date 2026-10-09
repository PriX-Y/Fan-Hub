import framebuf
import time
from machine import ADC, I2C, Pin, PWM


SET_CONTRAST = 0x81
SET_ENTIRE_ON = 0xA4
SET_NORM_INV = 0xA6
SET_DISP = 0xAE
SET_MEM_ADDR = 0x20
SET_COL_ADDR = 0x21
SET_PAGE_ADDR = 0x22
SET_DISP_START_LINE = 0x40
SET_SEG_REMAP = 0xA0
SET_MUX_RATIO = 0xA8
SET_COM_OUT_DIR = 0xC0
SET_DISP_OFFSET = 0xD3
SET_COM_PIN_CFG = 0xDA
SET_DISP_CLK_DIV = 0xD5
SET_PRECHARGE = 0xD9
SET_VCOM_DESEL = 0xDB
SET_CHARGE_PUMP = 0x8D


class SSD1306_I2C(framebuf.FrameBuffer):
    def __init__(self, width, height, i2c, addr=0x3C):
        self.width = width
        self.height = height
        self.i2c = i2c
        self.addr = addr
        self.buffer = bytearray(self.width * self.height // 8)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self.init_display()

    def write_cmd(self, cmd):
        self.i2c.writeto(self.addr, bytes([0x00, cmd]))

    def init_display(self):
        for cmd in (
            SET_DISP | 0x00,
            SET_MEM_ADDR,
            0x00,
            SET_DISP_START_LINE | 0x00,
            SET_SEG_REMAP | 0x01,
            SET_MUX_RATIO,
            self.height - 1,
            SET_COM_OUT_DIR | 0x08,
            SET_DISP_OFFSET,
            0x00,
            SET_COM_PIN_CFG,
            0x02 if self.height == 32 else 0x12,
            SET_DISP_CLK_DIV,
            0x80,
            SET_PRECHARGE,
            0xF1,
            SET_VCOM_DESEL,
            0x30,
            SET_CONTRAST,
            0xFF,
            SET_ENTIRE_ON,
            SET_NORM_INV,
            SET_CHARGE_PUMP,
            0x14,
            SET_DISP | 0x01,
        ):
            self.write_cmd(cmd)

    def show(self):
        self.write_cmd(SET_COL_ADDR)
        self.write_cmd(0)
        self.write_cmd(self.width - 1)
        self.write_cmd(SET_PAGE_ADDR)
        self.write_cmd(0)
        self.write_cmd((self.height // 8) - 1)
        self.i2c.writeto(self.addr, b"\x40" + self.buffer)


class FanController:
    def __init__(self, fan_id, pwm_pin, tach_pin, initial_duty=50):
        self.fan_id = fan_id
        self.pwm = PWM(Pin(pwm_pin))
        self.pwm.freq(25000)

        self.duty_percent = initial_duty
        self._update_pwm()

        self.tach_pin = Pin(tach_pin, Pin.IN, Pin.PULL_UP)
        self.rpm_count = 0
        self.last_rpm_time = time.ticks_ms()
        self.tach_pin.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=self._tach_isr)

    def _update_pwm(self):
        duty_u16 = int((self.duty_percent / 100.0) * 65535)
        self.pwm.duty_u16(duty_u16)

    def _tach_isr(self, pin):
        if pin.value():
            self.rpm_count += 1

    def get_rpm(self):
        now = time.ticks_ms()
        elapsed_ms = time.ticks_diff(now, self.last_rpm_time)
        if elapsed_ms >= 1000:
            rpm = (self.rpm_count * 60000) // (elapsed_ms * 2)
            self.rpm_count = 0
            self.last_rpm_time = now
            return rpm
        return 0

    def increase_duty(self, step=5):
        self.duty_percent = min(100, self.duty_percent + step)
        self._update_pwm()

    def decrease_duty(self, step=5):
        self.duty_percent = max(0, self.duty_percent - step)
        self._update_pwm()


class Encoder:
    def __init__(self, pin_a, pin_b, controller):
        self.pin_a = Pin(pin_a, Pin.IN, Pin.PULL_UP)
        self.pin_b = Pin(pin_b, Pin.IN, Pin.PULL_UP)
        self.controller = controller
        self.last_a = self.pin_a.value()
        self.pin_a.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=self._encoder_isr)

    def _encoder_isr(self, pin):
        current_a = self.pin_a.value()
        if current_a != self.last_a:
            if self.pin_b.value() != current_a:
                self.controller.increase_duty(5)
            else:
                self.controller.decrease_duty(5)
            self.last_a = current_a


i2c = I2C(0, scl=Pin(0), sda=Pin(1), freq=400000)
oled = SSD1306_I2C(128, 32, i2c)

temp_sensor = ADC(Pin(26))

FANS_CONFIG = [
    (1, 3, 2),
    (2, 7, 6),
    (3, 11, 10),
    (4, 15, 14),
]

controllers = [
    FanController(cfg[0], cfg[1], cfg[2]) for cfg in FANS_CONFIG
]

ENCODER_CONFIG = [
    (4, 5, controllers[0]),
    (8, 9, controllers[1]),
    (12, 13, controllers[2]),
    (16, 17, controllers[3]),
]

encoders = [
    Encoder(cfg[0], cfg[1], cfg[2]) for cfg in ENCODER_CONFIG
]


def read_temperature():
    raw_adc = temp_sensor.read_u16()
    voltage = (raw_adc / 65535.0) * 3.3
    temperature = voltage * 100.0
    return temperature


last_temp_update = 0
last_display_update = 0
temp_c = 0.0

while True:
    now = time.ticks_ms()

    if time.ticks_diff(now, last_temp_update) >= 100:
        last_temp_update = now
        temp_c = read_temperature()

    if time.ticks_diff(now, last_display_update) >= 200:
        last_display_update = now
        oled.fill(0)
        oled.text("TEMP: {:.1f} C".format(temp_c), 0, 0)

        f1_val = controllers[0].duty_percent
        f2_val = controllers[1].duty_percent
        oled.text("F1:{:3d}%  F2:{:3d}%".format(f1_val, f2_val), 0, 12)

        f3_val = controllers[2].duty_percent
        f4_val = controllers[3].duty_percent
        oled.text("F3:{:3d}%  F4:{:3d}%".format(f3_val, f4_val), 0, 24)

        oled.show()

    time.sleep(0.01)