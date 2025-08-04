$(function() {

    //首页焦点图
    $(".banner").slide({titCell: ".hd ul", mainCell: ".bd ul", effect: "fold", autoPage: true, autoPlay: true, delayTime: 700});
 
    //个人中心-文章切换
    $('.js-tabs').on('click', '.menus ul li', function(){
        $(this).children('a').addClass('current').parent('li').siblings().children('a').removeClass('current');
        var index = $(this).index();
        $(this).parents('.js-tabs').children('.tabs-cont').children('.list').removeClass('current').eq(index).addClass('current');//显示对应tabs
    }); 
}); 

//消息弹窗
function PopupSubmit(message){
    $('.popup-hint').remove();
    $('body').append('<div class="popup-hint"><span>'+ message +'</span></div>');
    $('.popup-hint').delay(2000).hide(0); //2秒隐藏
}
  
//回到顶部
function GoTop(){
    $('.gotop').click(function() {
        $('body,html').animate({
            scrollTop: 0
        }, 400);
        return false;
    });
} 