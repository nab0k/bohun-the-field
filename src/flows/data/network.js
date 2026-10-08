// TEST network for the grey "Flows" prototype. Every position is a placeholder chosen to exercise the mechanics;
// Serhii will say where each object really goes. Names are generic categories, never real operations.
// type: mine | factory-s | factory-m | factory-l | bank | port | station | airfield
export const TYPE_RU = {
  mine: 'Шахта', 'factory-s': 'Малый завод', 'factory-m': 'Средний завод', 'factory-l': 'Крупный завод',
  bank: 'Банк', port: 'Порт', station: 'Ж/д станция', airfield: 'Аэродром',
};
export const TYPE_ROLE = {
  mine: 'Источник сырья в цепочке.', 'factory-s': 'Производство: малый объём.', 'factory-m': 'Производство: средний объём.',
  'factory-l': 'Производство: крупный объём.', bank: 'Сюда возвращаются деньги за доставленный груз.',
  port: 'Узел перевалки: море.', station: 'Узел перевалки: железная дорога.', airfield: 'Узел перевалки: воздух.',
};

export const nodes = [
  // Ukraine (test positions)
  { id: 'mine-kr', type: 'mine', name: 'Шахта', place: 'Кривбас (тест)', lon: 33.4, lat: 47.9 },
  { id: 'fac-dnipro', type: 'factory-l', name: 'Крупный завод', place: 'Днепр (тест)', lon: 35.0, lat: 48.5 },
  { id: 'fac-kharkiv', type: 'factory-m', name: 'Средний завод', place: 'Харьков (тест)', lon: 36.2, lat: 50.0 },
  { id: 'fac-lviv', type: 'factory-s', name: 'Малый завод', place: 'Львов (тест)', lon: 24.0, lat: 49.8 },
  { id: 'bank-kyiv', type: 'bank', name: 'Банк', place: 'Киев (тест)', lon: 30.5, lat: 50.4 },
  { id: 'st-kyiv', type: 'station', name: 'Ж/д станция', place: 'Киев (тест)', lon: 30.9, lat: 50.2 },
  { id: 'st-lviv', type: 'station', name: 'Ж/д станция', place: 'Львов (тест)', lon: 24.3, lat: 49.7 },
  { id: 'port-odesa', type: 'port', name: 'Порт', place: 'Одесса (тест)', lon: 30.8, lat: 46.4 },
  { id: 'air-lviv', type: 'airfield', name: 'Аэродром', place: 'Львов (тест)', lon: 23.6, lat: 49.9 },
  { id: 'air-kyiv', type: 'airfield', name: 'Аэродром', place: 'Киев (тест)', lon: 30.2, lat: 50.7 },
  // Europe
  { id: 'st-warsaw', type: 'station', name: 'Ж/д станция', place: 'Варшава (тест)', lon: 21.0, lat: 52.2 },
  { id: 'st-berlin', type: 'station', name: 'Ж/д станция', place: 'Берлин (тест)', lon: 13.4, lat: 52.5 },
  { id: 'st-frankfurt', type: 'station', name: 'Ж/д станция', place: 'Франкфурт (тест)', lon: 8.7, lat: 50.0 },
  { id: 'air-frankfurt', type: 'airfield', name: 'Аэродром', place: 'Франкфурт (тест)', lon: 8.4, lat: 50.2 },
  { id: 'bank-frankfurt', type: 'bank', name: 'Банк', place: 'Франкфурт (тест)', lon: 9.0, lat: 50.3 },
  { id: 'port-rotterdam', type: 'port', name: 'Порт', place: 'Роттердам (тест)', lon: 4.1, lat: 51.95 },
  { id: 'port-istanbul', type: 'port', name: 'Порт', place: 'Стамбул (тест)', lon: 29.0, lat: 41.0 },
  // Sea gateways
  { id: 'port-suez', type: 'port', name: 'Порт', place: 'Суэц (тест)', lon: 32.55, lat: 29.95 },
  { id: 'port-aden', type: 'port', name: 'Порт', place: 'Аден (тест)', lon: 45.0, lat: 12.8 },
  { id: 'port-singapore', type: 'port', name: 'Порт', place: 'Сингапур (тест)', lon: 103.8, lat: 1.27 },
  // Asia
  { id: 'port-shanghai', type: 'port', name: 'Порт', place: 'Шанхай (тест)', lon: 121.8, lat: 30.9 },
  { id: 'air-shanghai', type: 'airfield', name: 'Аэродром', place: 'Шанхай (тест)', lon: 121.3, lat: 31.2 },
  { id: 'fac-shanghai', type: 'factory-l', name: 'Крупный завод', place: 'Шанхай (тест)', lon: 120.8, lat: 31.0 },
  { id: 'bank-shanghai', type: 'bank', name: 'Банк', place: 'Шанхай (тест)', lon: 121.5, lat: 31.4 },
  // North America
  { id: 'port-newyork', type: 'port', name: 'Порт', place: 'Нью-Йорк (тест)', lon: -74.0, lat: 40.6 },
  { id: 'bank-newyork', type: 'bank', name: 'Банк', place: 'Нью-Йорк (тест)', lon: -74.2, lat: 40.9 },
  { id: 'air-newark', type: 'airfield', name: 'Аэродром', place: 'Нью-Йорк (тест)', lon: -74.6, lat: 40.7 },
];

// corridors: mode rail | road | sea | air; via = optional [lon, lat] bend points
export const edges = [
  // rail
  { id: 'r1', mode: 'rail', a: 'st-lviv', b: 'st-kyiv' },
  { id: 'r2', mode: 'rail', a: 'st-kyiv', b: 'port-odesa' },
  { id: 'r3', mode: 'rail', a: 'st-lviv', b: 'st-warsaw' },
  { id: 'r4', mode: 'rail', a: 'st-warsaw', b: 'st-berlin' },
  { id: 'r5', mode: 'rail', a: 'st-berlin', b: 'st-frankfurt' },
  { id: 'r6', mode: 'rail', a: 'st-frankfurt', b: 'port-rotterdam' },
  // road (local links)
  { id: 'd1', mode: 'road', a: 'mine-kr', b: 'st-kyiv' },
  { id: 'd2', mode: 'road', a: 'fac-dnipro', b: 'st-kyiv' },
  { id: 'd3', mode: 'road', a: 'fac-kharkiv', b: 'st-kyiv' },
  { id: 'd4', mode: 'road', a: 'fac-lviv', b: 'st-lviv' },
  { id: 'd5', mode: 'road', a: 'bank-kyiv', b: 'st-kyiv' },
  { id: 'd6', mode: 'road', a: 'mine-kr', b: 'fac-dnipro' },
  { id: 'd7', mode: 'road', a: 'fac-dnipro', b: 'port-odesa' },
  { id: 'd8', mode: 'road', a: 'air-lviv', b: 'st-lviv' },
  { id: 'd9', mode: 'road', a: 'air-kyiv', b: 'bank-kyiv' },
  { id: 'd10', mode: 'road', a: 'air-frankfurt', b: 'st-frankfurt' },
  { id: 'd11', mode: 'road', a: 'bank-frankfurt', b: 'st-frankfurt' },
  { id: 'd12', mode: 'road', a: 'port-shanghai', b: 'fac-shanghai' },
  { id: 'd13', mode: 'road', a: 'port-shanghai', b: 'air-shanghai' },
  { id: 'd14', mode: 'road', a: 'port-shanghai', b: 'bank-shanghai' },
  { id: 'd15', mode: 'road', a: 'port-newyork', b: 'bank-newyork' },
  { id: 'd16', mode: 'road', a: 'port-newyork', b: 'air-newark' },
  // sea
  { id: 's1', mode: 'sea', a: 'port-odesa', b: 'port-istanbul', via: [[30.2, 44.5], [29.4, 41.6]] },
  { id: 's2', mode: 'sea', a: 'port-istanbul', b: 'port-suez', via: [[28.9, 40.7], [26.4, 40.2], [25.0, 38.4], [25.5, 34.3], [31.0, 32.3], [32.4, 31.4]] },
  { id: 's3', mode: 'sea', a: 'port-suez', b: 'port-aden', via: [[34.0, 27.4], [36.6, 22.4], [39.6, 17.2], [42.8, 13.3]] },
  { id: 's4', mode: 'sea', a: 'port-aden', b: 'port-singapore', via: [[52.0, 12.6], [60.0, 9.5], [72.0, 6.0], [80.4, 5.2], [92.0, 5.8], [98.0, 6.0], [100.0, 3.8], [102.4, 2.2]] },
  { id: 's5', mode: 'sea', a: 'port-singapore', b: 'port-shanghai', via: [[107.0, 4.0], [112.0, 12.0], [117.0, 17.5], [121.8, 21.0], [123.6, 25.0], [123.2, 29.6]] },
  { id: 's6', mode: 'sea', a: 'port-istanbul', b: 'port-rotterdam', via: [[28.9, 40.7], [26.4, 40.2], [25.0, 38.4], [21.0, 36.0], [12.2, 36.4], [8.0, 38.0], [-1.5, 36.5], [-5.6, 36.0], [-9.8, 37.0], [-10.2, 43.2], [-6.0, 48.2], [-1.0, 49.8], [2.0, 51.2]] },
  { id: 's7', mode: 'sea', a: 'port-rotterdam', b: 'port-newyork', via: [[2.0, 51.4], [-4.0, 49.6], [-15.0, 49.0], [-40.0, 45.0], [-65.0, 40.2]] },
  // air (straight lines; planes only between airfields)
  { id: 'a1', mode: 'air', a: 'air-lviv', b: 'air-frankfurt' },
  { id: 'a2', mode: 'air', a: 'air-kyiv', b: 'air-frankfurt' },
  { id: 'a3', mode: 'air', a: 'air-frankfurt', b: 'air-newark' },
  { id: 'a4', mode: 'air', a: 'air-frankfurt', b: 'air-shanghai' },
  { id: 'a5', mode: 'air', a: 'air-kyiv', b: 'air-shanghai' },
];

export const BOHUN_START = 'bank-kyiv';
