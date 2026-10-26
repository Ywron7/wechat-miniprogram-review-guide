Page({
  pick() {
    wx.chooseMedia({count: 1, sourceType: ['album']});
  }
});
